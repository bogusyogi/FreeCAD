# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCADCmd worker for export_gltf.py."""

import json
import math
import os
from pathlib import Path
import traceback
import xml.etree.ElementTree as ET
import zipfile

import FreeCAD as App
import Import
import Part


def gui_visibility(path):
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("GuiDocument.xml"))
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile):
        return {}
    result = {}
    for provider in root.findall("./ViewProviderData/ViewProvider"):
        value = provider.find('./Properties/Property[@name="Visibility"]/Bool')
        if value is not None:
            result[provider.get("name", "")] = value.get("value", "true").lower() == "true"
    return result


def gui_colors(path):
    colors = {}
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("GuiDocument.xml"))
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile):
        return colors
    for provider in root.findall("./ViewProviderData/ViewProvider"):
        for name in ("DiffuseColor", "ShapeColor"):
            prop = provider.find(f'./Properties/Property[@name="{name}"]')
            if prop is None:
                continue
            values = []
            for node in prop.iter():
                for key, value in node.attrib.items():
                    if key.lower() in {"r", "g", "b", "a", "red", "green", "blue", "alpha"}:
                        try:
                            values.append(float(value))
                        except ValueError:
                            pass
            if len(values) >= 3:
                colors[provider.get("name", "")] = values[:4] if len(values) >= 4 else values[:3] + [1.0]
                break
    return colors


def read_color_map(path):
    if not path:
        return {}, None
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("materials"), dict):
        raise ValueError("Color map must have schema 1 and a materials object")
    return data["materials"], {"path": str(source), **data.get("provenance", {})}


def linear_channel(value):
    value = max(0.0, min(1.0, float(value)))
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def shape_box(shape):
    box = shape.optimalBoundingBox(False, False)
    return [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax]


def cylinder_axes(obj):
    axes = []
    for index, face in enumerate(obj.Shape.Faces, 1):
        surface = face.Surface
        if isinstance(surface, Part.Cylinder):
            direction = surface.Axis
            direction.normalize()
            point = surface.Center
            axes.append({"face": index, "radius_mm": float(surface.Radius),
                         "direction": list(direction), "point_mm": list(point)})
    return axes


def measure_pivot(root):
    found = {}

    def visit(obj):
        if obj.Label in {"FENRIR_PIVOT", "FENRIR_BLADE_v1"} and hasattr(obj, "Shape"):
            found[obj.Label] = obj
        for child in getattr(obj, "Group", []):
            visit(child)

    visit(root)
    if set(found) != {"FENRIR_PIVOT", "FENRIR_BLADE_v1"}:
        return {"status": "no candidate measured", "stop_angles_deg": None}
    pins = [axis for axis in cylinder_axes(found["FENRIR_PIVOT"]) if abs(axis["radius_mm"] - 2.0) < 0.01]
    bores = [axis for axis in cylinder_axes(found["FENRIR_BLADE_v1"]) if abs(axis["radius_mm"] - 3.0) < 0.01]
    matches = []
    for pin in pins:
        for bore in bores:
            dot = sum(a * b for a, b in zip(pin["direction"], bore["direction"]))
            if abs(dot) < 0.9999:
                continue
            axis = pin["direction"] if dot >= 0 else [-v for v in pin["direction"]]
            delta = [b - a for a, b in zip(pin["point_mm"], bore["point_mm"])]
            axial = sum(a * b for a, b in zip(delta, axis))
            radial = math.sqrt(max(0.0, sum(v * v for v in delta) - axial * axial))
            if radial <= 0.01:
                matches.append((radial, axis, pin, bore))
    if not matches:
        return {"status": "no common analytic pin/bore axis", "stop_angles_deg": None}
    error, axis, pin, bore = min(matches, key=lambda item: item[0])
    first_nonzero = next((value for value in axis if abs(value) > 1e-9), 1.0)
    if first_nonzero < 0:
        axis = [-value for value in axis]
    point = [(a + b) / 2 for a, b in zip(pin["point_mm"], bore["point_mm"])]
    projection = sum(a * b for a, b in zip(point, axis))
    point = [value - projection * direction for value, direction in zip(point, axis)]
    return {"status": "axis measured from matching analytic cylindrical surfaces",
            "axis_line": {"point_mm_nearest_origin": point, "direction_unit": axis,
                          "coordinate_space": "FreeCAD world XYZ, millimetres"},
            "supporting_geometry": {"pin_component": "FENRIR_PIVOT", "pin_face": pin["face"],
                                    "pin_radius_mm": pin["radius_mm"],
                                    "blade_component": "FENRIR_BLADE_v1", "bore_face": bore["face"],
                                    "bore_radius_mm": bore["radius_mm"],
                                    "radial_axis_disagreement_mm": error},
            "stop_angles_deg": None,
            "stop_angle_status": "unmeasured: no native joint or collision sweep evidence"}


def tree_record(obj, visibility, color_map, gui_color_map, color_table, shape_labels):
    children = list(getattr(obj, "Group", []))
    record = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId,
              "visible": visibility.get(obj.Name, True),
              "placement_mm": {"translation": list(obj.Placement.Base),
                               "rotation_quaternion_xyzw": list(obj.Placement.Rotation.Q)}}
    if children:
        record["children"] = [tree_record(child, visibility, color_map, gui_color_map,
                                          color_table, shape_labels) for child in children]
    elif hasattr(obj, "Shape") and not obj.Shape.isNull():
        shape = obj.Shape
        vertices, triangles = shape.tessellate(0.1, False)
        obj.Shape = shape
        shape_labels.append(obj.Label)
        record["geometry"] = {"solids": len(shape.Solids), "faces": len(shape.Faces),
                              "edges": len(shape.Edges), "triangles": len(triangles),
                              "bounds_mm": shape_box(Part.getShape(obj))}
        appearance = color_map.get(obj.Label)
        if appearance is None:
            rgba = gui_color_map.get(obj.Name)
            if rgba is not None:
                appearance = {"name": "Saved FreeCAD appearance", "color_srgb": rgba}
        if appearance is None:
            appearance = {"name": "FreeCAD neutral fallback", "color_srgb": [0.8, 0.8, 0.8, 1.0]}
        srgb = list(appearance.get("color_srgb", [0.8, 0.8, 0.8, 1.0]))
        if len(srgb) == 3:
            srgb.append(1.0)
        color_table[obj.Label] = {"name": appearance.get("name", "Fixture material"),
                                  "rgba_srgb": srgb,
                                  "rgba_linear": [linear_channel(c) for c in srgb[:3]] + [float(srgb[3])],
                                  "provenance": appearance.get("provenance", "saved color map")}
    return record


def main():
    request = json.loads(Path(os.environ["FREECAD_GLTF_REQUEST"]).read_text(encoding="utf-8"))
    result_path = Path(os.environ["FREECAD_GLTF_REQUEST"]).with_name("result.json")
    document_path = Path(request["document"])
    output_path = Path(request["output"])
    doc = None
    try:
        doc = App.openDocument(str(document_path))
        doc.recompute()
        roots = [item for item in doc.RootObjects
                 if not Part.getShape(item).isNull() and Part.getShape(item).Solids]
        if len(roots) != 1:
            raise ValueError(f"Expected one shape-bearing assembly root, found {len(roots)}")
        root = roots[0]
        visibility = gui_visibility(document_path)
        color_map, color_provenance = read_color_map(request.get("color_map"))
        saved_colors = gui_colors(document_path)
        shape_labels = []
        color_table = {}
        tree = tree_record(root, visibility, color_map, saved_colors, color_table, shape_labels)
        Import.export([root], str(output_path), exportHidden=False, legacy=False, keepPlacement=True)
        root_shape = Part.getShape(root)
        upstream = None
        if request.get("source_step"):
            upstream_path = Path(request["source_step"])
            upstream = {"kind": "STEP", "path": str(upstream_path),
                        "sha256": __import__("hashlib").sha256(upstream_path.read_bytes()).hexdigest()}
        report = {"schema": 1, "source": {"path": str(document_path),
                                             "sha256": __import__("hashlib").sha256(document_path.read_bytes()).hexdigest(),
                                             "upstream_cad": upstream},
                  "freecad_version": App.Version(), "units": {"source_length": "mm",
                                                                  "glb_length": "m",
                                                                  "coordinate_space": "FreeCAD world XYZ; GLB writer converts to glTF Y-up"},
                  "assembly": tree,
                  "measurement": {"solids": len(root_shape.Solids), "faces": len(root_shape.Faces),
                                  "edges": len(root_shape.Edges), "bounds_mm": shape_box(root_shape)},
                  "shape_labels": shape_labels,
                  "appearance": {"source": color_provenance or
                                 ("FCStd GuiDocument.xml" if saved_colors else "FreeCAD neutral fallback; source has no saved colors"),
                                 "assigned_shape_count": len(color_table)},
                  "color_table": color_table,
                  "mechanism": measure_pivot(root)}
        result_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except Exception:
        result_path.write_text(json.dumps({"error": traceback.format_exc()}, indent=2), encoding="utf-8")
        raise
    finally:
        if doc is not None:
            App.closeDocument(doc.Name)


main()
