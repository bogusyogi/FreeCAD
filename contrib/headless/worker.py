# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD-side entry point. Executed by run.py, never by system Python."""

import json
import os
from pathlib import Path
import runpy
import traceback

import FreeCAD as App
import Part


def measure(shape):
    if shape.isNull() or not shape.isValid() or not shape.Solids:
        raise ValueError("Model must contain valid solid geometry")
    if any(not solid.isClosed() or solid.Volume <= 0 for solid in shape.Solids):
        raise ValueError("Model must contain closed, positive-volume solids")
    # BoundBox may include loose spline control bounds or cached tessellation.
    box = shape.optimalBoundingBox(False, False)
    return {
        "solids": len(shape.Solids),
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "size_mm": [box.XLength, box.YLength, box.ZLength],
        "volume_mm3": sum(solid.Volume for solid in shape.Solids),
        "volume_method": "FreeCAD default integration; no numerical error bound",
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
        original = measure(obj.Shape)
        # Publish one result in GUI while retaining editable construction features.
        for item in doc.Objects:
            if "Visibility" in item.PropertiesList:
                item.Visibility = item == obj
        name = obj.Name
        doc.saveAs(str(out / "model.FCStd"))
        Part.export([obj], str(out / "model.step"))
        App.closeDocument(doc.Name)
        reopened = App.openDocument(str(out / "model.FCStd"))
        reopened.recompute()
        native = measure(reopened.getObject(name).Shape)
        step = Part.read(str(out / "model.step"))
        imported = measure(step)
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
