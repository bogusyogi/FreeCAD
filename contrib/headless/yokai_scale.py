# SPDX-License-Identifier: LGPL-2.1-or-later
"""Create a native, parameterized shallow recess in Yokai mini.STEP."""

import hashlib
import json
import math
import os
from pathlib import Path

import FreeCAD as App
import Import
import Part


RECESS_SIZE_MM = 2.0
TOOL_OVERTRAVEL_MM = 0.1


def _shape(item):
    return Part.getShape(item)


def _leaves(item):
    children = list(getattr(item, "Group", []))
    if children:
        result = []
        for child in children:
            result.extend(_leaves(child))
        return result
    shape = _shape(item)
    return [(item, shape)] if not shape.isNull() and shape.Solids else []


def _bounds(shape):
    box = shape.optimalBoundingBox(False, False)
    return [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax]


def _placement(item):
    base = item.Placement.Base
    rotation = item.Placement.Rotation.Q
    return [base.x, base.y, base.z, *rotation]


def _records(items):
    return {
        item.Name: {
            "label": item.Label,
            "volume_mm3": shape.Volume,
            "bounds_mm": _bounds(shape),
            "placement": _placement(item),
            "brep_sha256": hashlib.sha256(shape.exportBrepToString().encode("latin-1")).hexdigest(),
        }
        for item, shape in items
    }


def _same_records(expected, actual, tolerance=1e-6):
    if set(expected) != set(actual):
        raise ValueError("Unedited Yokai assembly members changed")
    for name, before in expected.items():
        after = actual[name]
        if before["label"] != after["label"]:
            raise ValueError(f"Unedited member label changed: {name}")
        if before["brep_sha256"] != after["brep_sha256"]:
            raise ValueError(f"Unedited member BRep changed: {name}")
        for key in ("volume_mm3", "bounds_mm", "placement"):
            left, right = before[key], after[key]
            if isinstance(left, list):
                if len(left) != len(right) or any(abs(a - b) > tolerance for a, b in zip(left, right)):
                    raise ValueError(f"Unedited member {key} changed: {name}")
            elif abs(left - right) > tolerance:
                raise ValueError(f"Unedited member {key} changed: {name}")


def _mount_faces(shape):
    """Find every imported 2 mm Y-axis mount-hole reference face."""
    found = []
    for face in shape.Faces:
        surface = face.Surface
        if not (hasattr(surface, "Radius") and hasattr(surface, "Axis") and hasattr(surface, "Center")):
            continue
        axis = surface.Axis
        if not (1.8 <= surface.Radius <= 2.1 and abs(axis.y) >= 0.999):
            continue
        found.append((face, [surface.Radius, surface.Center.x, surface.Center.z, axis.y]))
    found.sort(key=lambda value: tuple(round(number, 6) for number in value[1]))
    return found


def _top_face(shape):
    choices = []
    for face in shape.Faces:
        if face.Area < 100 or not isinstance(face.Surface, Part.Plane):
            continue
        normal = face.normalAt(0, 0)
        if normal.y > 0.999:
            choices.append(face)
    if not choices:
        raise ValueError("Yokai scale has no broad exposed +Y planar face")
    return max(choices, key=lambda face: face.CenterOfMass.y)


def _mating_probe(shape):
    faces = []
    for face in shape.Faces:
        if not isinstance(face.Surface, Part.Plane) or face.Area < 100:
            continue
        normal = face.normalAt(0, 0)
        if normal.y < -0.999:
            faces.append(face)
    if not faces:
        raise ValueError("Yokai scale has no broad -Y mating face")
    face = max(faces, key=lambda candidate: candidate.Area)
    normal = face.normalAt(0, 0)
    return face, face.extrude(-normal * 0.05)


def _valid_solid(shape):
    if shape.isNull() or not shape.isValid() or not shape.Solids:
        raise ValueError("Yokai edit did not produce valid solid geometry")
    if any(not solid.isClosed() or solid.Volume <= 0 for solid in shape.Solids):
        raise ValueError("Yokai edit did not produce closed positive solids")


def _request_output():
    request = json.loads(Path(os.environ["FREECAD_MODEL_REQUEST"]).read_text(encoding="utf-8"))
    return Path(request["out"])


def _write_evidence(name, evidence):
    (_request_output() / name).write_text(json.dumps(evidence, indent=2), encoding="utf-8")


def _number(params, key, default):
    value = float(params.get(key, default))
    if not math.isfinite(value):
        raise ValueError(f"{key} must be finite")
    return value


def build(params):
    allowed = {"source", "recess_depth"}
    if set(params) - allowed:
        raise ValueError(f"Unknown parameters: {sorted(set(params) - allowed)}")
    source = Path(params["source"]).resolve(strict=True)
    depth = _number(params, "recess_depth", 0.5)
    if not 0.1 <= depth <= 1.0:
        raise ValueError("recess_depth must be within the validated 0.1..1.0 mm range")

    doc = App.newDocument("YokaiScale")
    Import.insert(str(source), doc.Name, merge=False, useLinkGroup=False, mode=0)
    doc.recompute()
    roots = [item for item in doc.RootObjects if not _shape(item).isNull()]
    if len(roots) != 1:
        raise ValueError(f"Expected one Yokai assembly root, found {len(roots)}")
    root = roots[0]
    leaves = _leaves(root)
    if len(leaves) < 7:
        raise ValueError("Yokai import did not retain expected multi-solid assembly")
    scale_candidates = [(item, shape) for item, shape in leaves if item.Label == "FIXED_20205_v04"]
    if len(scale_candidates) != 1:
        raise ValueError("Expected one imported Yokai scale labelled FIXED_20205_v04")
    target, source_shape = scale_candidates[0]
    if source_shape.Volume <= 1000:
        raise ValueError("Yokai scale candidate is unexpectedly small")
    parent = next((item for item in doc.Objects if target in getattr(item, "Group", [])), None)
    if parent is None:
        raise ValueError("Yokai scale candidate has no assembly parent")
    if not root.Placement.isIdentity() or not parent.Placement.isIdentity():
        raise ValueError("Yokai imported root or scale parent has unsupported non-identity placement")
    target_label = target.Label

    face = _top_face(source_shape)
    centre = face.CenterOfMass
    mount_faces = _mount_faces(source_shape)
    if len(mount_faces) < 3:
        raise ValueError("Expected at least three 2 mm Yokai mount-hole reference faces")
    other_before = _records([(item, shape) for item, shape in leaves if item != target])
    mating_face, mating_probe = _mating_probe(source_shape)

    control = doc.addObject("App::FeaturePython", "YokaiParameters")
    control.addProperty("App::PropertyString", "Source", "Yokai")
    control.Source = str(source)
    control.addProperty("App::PropertyString", "SourceSha256", "Yokai")
    control.SourceSha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    for name, value in (("RecessDepth", depth), ("RecessSize", RECESS_SIZE_MM),
                        ("RecessCentreZ", centre.z), ("ScaleTopY", centre.y)):
        control.addProperty("App::PropertyLength", name, "Yokai")
        setattr(control, name, value)
    control.addProperty("App::PropertyFloat", "RecessCentreX", "Yokai")
    control.RecessCentreX = centre.x
    control.addProperty("App::PropertyString", "TargetName", "Yokai")
    control.TargetName = target.Name
    control.addProperty("App::PropertyString", "RootName", "Yokai")
    control.RootName = root.Name

    tool = doc.addObject("Part::Box", "YokaiRecessTool")
    tool.Label = "Yokai shallow recess tool"
    tool.setExpression("Length", "YokaiParameters.RecessSize")
    tool.setExpression("Height", "YokaiParameters.RecessSize")
    tool.setExpression("Width", f"YokaiParameters.RecessDepth + {TOOL_OVERTRAVEL_MM} mm")
    tool.setExpression("Placement.Base.x", "YokaiParameters.RecessCentreX * 1 mm - YokaiParameters.RecessSize / 2")
    tool.setExpression("Placement.Base.y", "YokaiParameters.ScaleTopY - YokaiParameters.RecessDepth")
    tool.setExpression("Placement.Base.z", "YokaiParameters.RecessCentreZ - YokaiParameters.RecessSize / 2")
    members = list(parent.Group)
    target.Label = f"{target_label} source"
    result = doc.addObject("Part::Cut", "YokaiScaleEdited")
    result.Label = target_label
    parent.addObject(result)
    result.Base, result.Tool = target, tool
    result.Refine = False
    parent.removeObject(target)
    parent.Group = [result if item == target else item for item in members]
    root.addProperty("App::PropertyBool", "HeadlessAssembly", "Yokai")
    root.HeadlessAssembly = True
    root.addProperty("App::PropertyString", "YokaiOperation", "Yokai")
    root.YokaiOperation = "2 mm square shallow recess on imported scale +Y face"
    target.Visibility = False
    tool.Visibility = False
    doc.recompute()

    _valid_solid(result.Shape)
    intersection = source_shape.common(tool.Shape)
    expected_removed = RECESS_SIZE_MM * RECESS_SIZE_MM * depth
    if abs(intersection.Volume - expected_removed) > 1e-5:
        raise ValueError("Recess tool is not fully supported by imported Yokai scale face")
    removed = source_shape.Volume - result.Shape.Volume
    if abs(removed - intersection.Volume) > 1e-4:
        raise ValueError(f"Native Yokai boolean volume mismatch: removed={removed}, intersection={intersection.Volume}")
    after_mount = _mount_faces(result.Shape)
    before_signatures = [signature for _, signature in mount_faces]
    after_signatures = [signature for _, signature in after_mount]
    if len(after_signatures) != len(before_signatures) or any(
        any(abs(a - b) > 1e-6 for a, b in zip(left, right))
        for left, right in zip(before_signatures, after_signatures)
    ):
        raise ValueError("Yokai mount-hole reference geometry changed")
    clearance = min(face.distToShape(tool.Shape)[0] for face, _ in mount_faces)
    if clearance <= 0.5:
        raise ValueError("Recess tool is too close to Yokai mount-hole reference geometry")
    other_after = _records([(item, _shape(item)) for item, _ in leaves if item != target])
    _same_records(other_before, other_after)
    mating_before = source_shape.common(mating_probe)
    mating_after = result.Shape.common(mating_probe)
    mating_delta = mating_before.cut(mating_after).Volume + mating_after.cut(mating_before).Volume
    if mating_delta > 1e-8:
        raise ValueError("Yokai mating-face BRep probe changed")

    evidence = {
        "source_sha256": control.SourceSha256,
        "root": {"name": root.Name, "label": root.Label},
        "target": {"name": target.Name, "label": target_label, "volume_mm3": source_shape.Volume},
        "top_face": {"area_mm2": face.Area, "centre_mm": [centre.x, centre.y, centre.z]},
        "mating_face": {"area_mm2": mating_face.Area,
                        "centre_mm": [mating_face.CenterOfMass.x, mating_face.CenterOfMass.y,
                                      mating_face.CenterOfMass.z],
                        "probe_symmetric_difference_mm3": mating_delta},
        "recess": {"size_mm": RECESS_SIZE_MM, "depth_mm": depth,
                   "expected_removed_mm3": expected_removed, "intersection_mm3": intersection.Volume,
                   "removed_mm3": removed},
        "mount_holes": before_signatures,
        "mount_hole_clearance_mm": clearance,
        "other_parts": other_before,
    }
    root.addProperty("App::PropertyString", "YokaiEvidence", "Yokai")
    root.YokaiEvidence = json.dumps(evidence, separators=(",", ":"))
    output = root
    _valid_solid(Part.getShape(output))
    if len(Part.getShape(output).Solids) != len(leaves):
        raise ValueError("Yokai output did not preserve assembly solid count")
    _write_evidence("yokai-operation.json", evidence)
    return output
