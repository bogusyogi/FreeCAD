# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD-side entry point. Executed by run.py, never by system Python."""

import json
import math
import os
from pathlib import Path
import runpy
import traceback
import xml.etree.ElementTree as ET
import zipfile

import FreeCAD as App
import Import
import Part


def visible_results(result):
    """Assembly children stay visible; construction dependencies stay hidden."""
    items = [result]
    for child in getattr(result, "Group", []):
        items.extend(visible_results(child))
    return items


def assembly_tree(result):
    """Record names, nesting & placed geometry without counting containers twice."""
    def visit(item, path):
        children = getattr(item, "Group", [])
        record = {"label": item.Label}
        if children:
            record["children"] = [visit(child, path + child.Name + ".") for child in children]
        else:
            shape = Part.getShape(result, path)
            box = shape.optimalBoundingBox(False, False)
            record.update(solids=len(shape.Solids), bounds_mm=[box.XMin, box.YMin, box.ZMin,
                                                            box.XMax, box.YMax, box.ZMax])
        return record
    return visit(result, "")


def check_tree(original, restored):
    if original["label"] != restored["label"]:
        raise ValueError("Assembly part label changed during roundtrip")
    a, b = original.get("children", []), restored.get("children", [])
    if len(a) != len(b):
        raise ValueError("Assembly structure changed during roundtrip")
    if a:
        for left, right in zip(a, b):
            check_tree(left, right)
    elif original["solids"] != restored["solids"] or any(
        abs(x - y) > 1e-5 for x, y in zip(original["bounds_mm"], restored["bounds_mm"])
    ):
        raise ValueError("Assembly component geometry or placement changed during roundtrip")


def export_step(result, path):
    """Bake a placed assembly root into its children for OCCT STEP export.

    FreeCAD's exporter retains child placements but drops a sole assembly root's
    placement. Abort the temporary transaction so the editable document is intact.
    """
    doc = result.Document
    children = getattr(result, "Group", [])
    placement = getattr(result, "Placement", App.Placement())
    relocate = bool(children) and not placement.isIdentity()
    if relocate:
        doc.openTransaction("Temporary STEP root placement")
    try:
        if relocate:
            for child in children:
                child.Placement = placement.multiply(child.Placement)
            result.Placement = App.Placement()
            doc.recompute()
        Import.export([result], str(path), exportHidden=False, legacy=False, keepPlacement=True)
    finally:
        if relocate:
            doc.abortTransaction()
            doc.recompute()


def save_view(path, objects, result):
    """Persist a minimal GUI view for stable runtimes that hide headless files."""
    root = ET.Element("Document", SchemaVersion="1")
    providers = ET.SubElement(root, "ViewProviderData", Count=str(len(objects)))
    visible = visible_results(result)
    for item in objects:
        provider = ET.SubElement(providers, "ViewProvider", name=item.Name)
        properties = ET.SubElement(provider, "Properties", Count="1")
        prop = ET.SubElement(properties, "Property", name="Visibility", type="App::PropertyBool")
        ET.SubElement(prop, "Bool", value="true" if item in visible else "false")
    box = Part.getShape(result).optimalBoundingBox(False, False)
    radius = max(box.DiagonalLength / 2, 1e-3)
    centre = box.Center
    distance = radius * 4
    offset = distance / (3 ** 0.5)
    camera = ("OrthographicCamera {\n"
              f"position {centre.x + offset} {centre.y - offset} {centre.z + offset}\n"
              "orientation 0.74290609 0.30772209 0.59447283 1.2171158\n"
              f"nearDistance {distance - radius * 2}\nfarDistance {distance + radius * 2}\n"
              f"focalDistance {distance}\nheight {radius * 2.6}\n}}\n")
    ET.SubElement(root, "Camera", settings=camera)
    # FreeCADCmd writes no GuiDocument.xml, including when revising a GUI file.
    with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        if "GuiDocument.xml" in archive.namelist():
            raise ValueError("Unexpected GUI metadata in headless output")
        archive.writestr("GuiDocument.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True))


def measure(shape, require_adaptive=False):
    if shape.isNull() or not shape.isValid() or not shape.Solids:
        raise ValueError("Model must contain valid solid geometry")
    if any(not solid.isClosed() or solid.Volume <= 0 for solid in shape.Solids):
        raise ValueError("Model must contain closed, positive-volume solids")
    # BoundBox may include loose spline control bounds or cached tessellation.
    box = shape.optimalBoundingBox(False, False)
    adaptive = hasattr(shape, "getVolumeProperties")
    if require_adaptive and not adaptive:
        raise ValueError("Adaptive volume requires a compiled fork with getVolumeProperties")
    eps = 1e-6
    properties = [solid.getVolumeProperties(eps) for solid in shape.Solids] if adaptive else []
    if any(not math.isfinite(value) or value <= 0 or not math.isfinite(error)
           or error < 0 or error > eps for value, error in properties):
        raise ValueError("Adaptive volume integration did not meet the requested precision")
    volume = sum(value for value, _ in properties) if adaptive else sum(solid.Volume for solid in shape.Solids)
    estimated_error = sum(abs(value * error) for value, error in properties) if adaptive else None
    return {
        "solids": len(shape.Solids),
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "size_mm": [box.XLength, box.YLength, box.ZLength],
        "volume_mm3": volume,
        "volume_method": "OCCT adaptive per-solid integration" if adaptive else "FreeCAD default integration; unqualified",
        "volume_eps": eps if adaptive else None,
        "volume_estimated_error_mm3": estimated_error,
        "volume_warning": None if adaptive else "Default integration can overstate curved-solid volume; use --require-adaptive for measurements",
    }


def main():
    request = json.loads(Path(os.environ["FREECAD_MODEL_REQUEST"]).read_text(encoding="utf-8"))
    out = Path(request["out"])
    try:
        namespace = runpy.run_path(request["model"])
        obj = namespace["build"](request["params"])
        doc = obj.Document
        doc.recompute()
        errors = [item.Name for item in doc.Objects if "Invalid" in item.State]
        if errors:
            raise ValueError(f"Invalid document objects: {errors}")
        require_adaptive = request.get("require_adaptive", False)
        original = measure(Part.getShape(obj), require_adaptive)
        tree = assembly_tree(obj) if getattr(obj, "HeadlessAssembly", False) else None
        visible = visible_results(obj)
        visible_names = [item.Name for item in visible]
        for item in doc.Objects:
            if "Visibility" in item.PropertiesList:
                item.Visibility = item in visible
        name = obj.Name
        doc.saveAs(str(out / "model.FCStd"))
        save_view(out / "model.FCStd", doc.Objects, obj)
        export_step(obj, out / "model.step")
        if tree:
            check_tree(tree, assembly_tree(obj))
        App.closeDocument(doc.Name)
        reopened = App.openDocument(str(out / "model.FCStd"))
        reopened.recompute()
        native = measure(Part.getShape(reopened.getObject(name)), require_adaptive)
        if tree:
            check_tree(tree, assembly_tree(reopened.getObject(name)))
            step_doc = App.newDocument("StepRoundtrip")
            Import.insert(str(out / "model.step"), step_doc.Name,
                          merge=False, useLinkGroup=False, mode=0)
            step_doc.recompute()
            roots = [item for item in step_doc.RootObjects if not Part.getShape(item).isNull()]
            if len(roots) != 1:
                raise ValueError("Assembly root count changed during STEP roundtrip")
            check_tree(tree, assembly_tree(roots[0]))
            App.closeDocument(step_doc.Name)
        step = Part.read(str(out / "model.step"))
        imported = measure(step, require_adaptive)
        # Default integration can change with STEP surface reparameterization.
        # Report every drift beyond numerical noise; 10 ppm is a serialization
        # sanity threshold, not a geometric tolerance or integration bound.
        roundtrips = {}
        for stage, candidate in (("native", native), ("STEP", imported)):
            if candidate["solids"] != original["solids"]:
                raise ValueError("Solid count changed during roundtrip")
            delta = abs(candidate["volume_mm3"] - original["volume_mm3"])
            relative = delta / original["volume_mm3"]
            roundtrips[stage] = {"volume_delta_mm3": delta, "volume_relative_delta": relative,
                                 "volume_drift_warning": delta > 1e-5 and relative > 1e-7}
            if delta > max(1e-5, original["volume_mm3"] * 1e-5):
                raise ValueError(f"Volume changed during {stage} roundtrip: {original['volume_mm3']} -> {candidate['volume_mm3']} mm^3")
            if any(abs(a - b) > 1e-5 for a, b in zip(candidate["size_mm"], original["size_mm"])):
                raise ValueError(f"Dimensions changed during {stage} roundtrip: {original['size_mm']} -> {candidate['size_mm']} mm")
        report = {
            "ok": True,
            "freecad_version": App.Version(),
            "result_object": name,
            "measurement": original,
            "assembly": tree,
            "visible_objects": visible_names,
            "native_roundtrip": native,
            "step_roundtrip": imported,
            "roundtrip_comparison": roundtrips,
            "roundtrip_relative_volume_limit": 1e-5,
            "files": {"document": str(out / "model.FCStd"), "step": str(out / "model.step")},
        }
        App.closeDocument(reopened.Name)
    except Exception as exc:
        report = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
    (out / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


main()
