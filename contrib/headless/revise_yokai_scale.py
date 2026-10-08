# SPDX-License-Identifier: LGPL-2.1-or-later
"""Reopen a Yokai recess document & change its native depth parameter."""

import json
import hashlib
import math
import os
from pathlib import Path

import FreeCAD as App
import Import
import Part


def _request_output():
    request = json.loads(Path(os.environ["FREECAD_MODEL_REQUEST"]).read_text(encoding="utf-8"))
    return Path(request["out"])


def _shape(item):
    return Part.getShape(item)


def _leaves(item):
    children = list(getattr(item, "Group", []))
    if children:
        return [leaf for child in children for leaf in _leaves(child)]
    shape = _shape(item)
    return [(item, shape)] if not shape.isNull() and shape.Solids else []


def _bounds(shape):
    box = shape.optimalBoundingBox(False, False)
    return [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax]


def _placement(item):
    base = item.Placement.Base
    return [base.x, base.y, base.z, *item.Placement.Rotation.Q]


def _records(items):
    return {item.Name: {"label": item.Label, "volume_mm3": shape.Volume,
                        "bounds_mm": _bounds(shape), "placement": _placement(item),
                        "brep_sha256": hashlib.sha256(shape.exportBrepToString().encode("latin-1")).hexdigest()}
            for item, shape in items}


def _same_records(expected, actual):
    if set(expected) != set(actual):
        raise ValueError("Unedited Yokai assembly members changed after reopen")
    for name, before in expected.items():
        after = actual[name]
        if before["label"] != after["label"]:
            raise ValueError(f"Unedited member label changed: {name}")
        for key in ("volume_mm3", "bounds_mm", "placement"):
            left, right = before[key], after[key]
            if isinstance(left, list):
                if len(left) != len(right) or any(abs(a - b) > 1e-6 for a, b in zip(left, right)):
                    raise ValueError(f"Unedited member {key} changed: {name}")
            elif abs(left - right) > 1e-6:
                raise ValueError(f"Unedited member {key} changed: {name}")


def _mount_signatures(shape):
    signatures = []
    for face in shape.Faces:
        surface = face.Surface
        if (hasattr(surface, "Radius") and hasattr(surface, "Axis") and hasattr(surface, "Center")
                and 1.8 <= surface.Radius <= 2.1 and abs(surface.Axis.y) >= 0.999
                ):
            signatures.append([surface.Radius, surface.Center.x, surface.Center.z, surface.Axis.y])
    return sorted(signatures, key=lambda signature: tuple(round(number, 6) for number in signature))


def _valid_solid(shape):
    if shape.isNull() or not shape.isValid() or not shape.Solids:
        raise ValueError("Revised Yokai geometry is invalid")
    if any(not solid.isClosed() or solid.Volume <= 0 for solid in shape.Solids):
        raise ValueError("Revised Yokai geometry is not a closed positive solid")


def _check_fresh_source_members(source, expected, current):
    """Compare every untouched reopened member with an independent STEP import."""
    reference = App.newDocument("YokaiReference")
    try:
        Import.insert(source, reference.Name, merge=False, useLinkGroup=False, mode=0)
        reference.recompute()
        roots = [item for item in reference.RootObjects if not _shape(item).isNull()]
        if len(roots) != 1:
            raise ValueError("Fresh Yokai source import has unexpected root count")
        by_label = {}
        for item, shape in _leaves(roots[0]):
            by_label.setdefault(item.Label, []).append(shape)
        for name, before in expected.items():
            matches = by_label.get(before["label"], [])
            if len(matches) != 1:
                raise ValueError(f"Fresh Yokai source cannot uniquely identify {before['label']}")
            left, right = current[name]["shape"], matches[0]
            delta = left.cut(right).Volume + right.cut(left).Volume
            if delta > max(1e-6, right.Volume * 1e-8):
                raise ValueError(f"Unedited member BRep differs from fresh source: {name}")
    finally:
        App.closeDocument(reference.Name)


def _mating_probe(shape):
    faces = []
    for face in shape.Faces:
        if not isinstance(face.Surface, Part.Plane) or face.Area < 100:
            continue
        normal = face.normalAt(0, 0)
        if normal.y < -0.999:
            faces.append(face)
    if not faces:
        raise ValueError("Reopened Yokai scale has no broad -Y mating face")
    face = max(faces, key=lambda candidate: candidate.Area)
    return face.extrude(-face.normalAt(0, 0) * 0.05)


def build(params):
    if set(params) - {"document", "recess_depth"}:
        raise ValueError(f"Unknown parameters: {sorted(set(params) - {'document', 'recess_depth'})}")
    document = Path(params["document"]).resolve(strict=True)
    depth = float(params.get("recess_depth", 0.75))
    if not math.isfinite(depth) or not 0.1 <= depth <= 1.0:
        raise ValueError("recess_depth must be within the validated 0.1..1.0 mm range")
    doc = App.openDocument(str(document))
    control = doc.getObject("YokaiParameters")
    if control is None:
        raise ValueError("Document has no Yokai native parameter object")
    root = doc.getObject(control.RootName)
    source = doc.getObject(control.TargetName)
    result = doc.getObject("YokaiScaleEdited")
    tool = doc.getObject("YokaiRecessTool")
    output = root
    if None in (root, source, result, tool) or result.Base != source or result.Tool != tool:
        raise ValueError("Document does not contain expected Yokai native cut tree")
    evidence = json.loads(root.YokaiEvidence)
    before_depth = control.RecessDepth.Value
    output_before = Part.getShape(output).Volume
    control.RecessDepth = depth
    doc.recompute()
    _valid_solid(result.Shape)
    expected_removed = control.RecessSize.Value ** 2 * depth
    intersection = source.Shape.common(tool.Shape)
    removed = source.Shape.Volume - result.Shape.Volume
    if abs(intersection.Volume - expected_removed) > 1e-5 or abs(removed - intersection.Volume) > 1e-4:
        raise ValueError("Reopened Yokai recess does not match native tool intersection")
    current_mounts = _mount_signatures(result.Shape)
    expected_mounts = evidence["mount_holes"]
    if len(current_mounts) != len(expected_mounts) or any(
        any(abs(a - b) > 1e-6 for a, b in zip(left, right))
        for left, right in zip(current_mounts, expected_mounts)
    ):
        raise ValueError("Yokai mount-hole reference geometry changed after reopen")
    untouched_pairs = [(doc.getObject(name), _shape(doc.getObject(name))) for name in evidence["other_parts"]]
    untouched = _records(untouched_pairs)
    _same_records(evidence["other_parts"], untouched)
    current_members = {item.Name: {"shape": shape} for item, shape in untouched_pairs}
    _check_fresh_source_members(control.Source, evidence["other_parts"], current_members)
    probe = _mating_probe(source.Shape)
    mating_before = source.Shape.common(probe)
    mating_after = result.Shape.common(probe)
    mating_delta = mating_before.cut(mating_after).Volume + mating_after.cut(mating_before).Volume
    if mating_delta > 1e-8:
        raise ValueError("Yokai mating-face BRep probe changed after reopen")
    output_shape = Part.getShape(output)
    _valid_solid(output_shape)
    if len(output_shape.Solids) != len(evidence["other_parts"]) + len(result.Shape.Solids):
        raise ValueError("Reopened Yokai output lost assembly solids")
    if not output_shape.Volume < output_before:
        raise ValueError("Reopened Yokai parameter did not update output geometry")
    revision = {"document": str(document), "before_depth_mm": before_depth, "after_depth_mm": depth,
                "intersection_mm3": intersection.Volume, "removed_mm3": removed,
                "mount_holes": current_mounts, "other_parts": untouched,
                "mating_probe_symmetric_difference_mm3": mating_delta,
                "output_volume_before_mm3": output_before,
                "output_volume_after_mm3": output_shape.Volume}
    root.addProperty("App::PropertyString", "YokaiRevisionEvidence", "Yokai")
    root.YokaiRevisionEvidence = json.dumps(revision, separators=(",", ":"))
    _request_output().joinpath("yokai-revision.json").write_text(json.dumps(revision, indent=2), encoding="utf-8")
    return output
