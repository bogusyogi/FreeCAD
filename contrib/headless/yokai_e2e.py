# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run real Yokai import, native edit, reopen/revise & STEP checks."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(root, out, name, model, freecad, params, require_adaptive):
    command = [sys.executable, str(root / "run.py"), str(root / model), "--freecad", str(freecad),
               "--out", str(out / name), "--params", json.dumps(params)]
    if require_adaptive:
        command.append("--require-adaptive")
    process = subprocess.run(command, capture_output=True, text=True, timeout=240,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    report_path = out / name / "result.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    if process.returncode or not report or not report.get("ok"):
        raise RuntimeError(f"{name} failed:\n{process.stdout}\n{process.stderr}\n{report}")
    return report


def _expect_failure(root, out, name, model, freecad, params, require_adaptive):
    command = [sys.executable, str(root / "run.py"), str(root / model), "--freecad", str(freecad),
               "--out", str(out / name), "--params", json.dumps(params)]
    if require_adaptive:
        command.append("--require-adaptive")
    process = subprocess.run(command, capture_output=True, text=True, timeout=240,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    report = json.loads((out / name / "result.json").read_text(encoding="utf-8"))
    if process.returncode == 0 or report.get("ok"):
        raise RuntimeError(f"{name} unexpectedly passed")
    return report["error"]


def _close(left, right, tolerance=1e-5):
    return math.isclose(left, right, abs_tol=tolerance, rel_tol=1e-8)


def _same_signatures(left, right):
    return len(left) == len(right) and all(
        len(first) == len(second) and all(_close(a, b, 1e-6) for a, b in zip(first, second))
        for first, second in zip(left, right)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freecad", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--require-adaptive", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    freecad = args.freecad.resolve(strict=True)
    args.out.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    source_digest = _digest(source)

    initial = _run(root, args.out, "initial", "yokai_scale.py", freecad,
                   {"source": str(source), "recess_depth": 0.5}, args.require_adaptive)
    operation = json.loads((args.out / "initial" / "yokai-operation.json").read_text(encoding="utf-8"))
    original_document = Path(initial["files"]["document"])
    original_document_digest = _digest(original_document)
    if operation["source_sha256"] != source_digest or _digest(source) != source_digest:
        raise RuntimeError("Yokai source STEP changed during native import")
    if operation["recess"]["removed_mm3"] <= 0 or not _close(
            operation["recess"]["removed_mm3"], operation["recess"]["expected_removed_mm3"], 1e-4):
        raise RuntimeError("Initial Yokai native cut did not remove expected geometry")
    if operation["mount_hole_clearance_mm"] <= 0.5 or len(operation["mount_holes"]) < 3:
        raise RuntimeError("Initial Yokai mount-hole clearance evidence is insufficient")

    revised = _run(root, args.out, "revised", "revise_yokai_scale.py", freecad,
                   {"document": str(original_document), "recess_depth": 0.75}, args.require_adaptive)
    revision = json.loads((args.out / "revised" / "yokai-revision.json").read_text(encoding="utf-8"))
    if _digest(original_document) != original_document_digest:
        raise RuntimeError("Independent Yokai revision changed original FCStd")
    if _digest(source) != source_digest:
        raise RuntimeError("Independent Yokai revision changed source STEP")
    if not revision["after_depth_mm"] > revision["before_depth_mm"]:
        raise RuntimeError("Reopened Yokai parameter did not increase recess depth")
    if not revision["removed_mm3"] > operation["recess"]["removed_mm3"]:
        raise RuntimeError("Reopened Yokai parameter did not change native geometry")
    for stage in ("measurement", "native_roundtrip", "step_roundtrip"):
        if initial[stage]["solids"] != revised[stage]["solids"]:
            raise RuntimeError(f"Yokai solid count changed after parameter edit: {stage}")
        if any(not _close(a, b) for a, b in zip(initial[stage]["size_mm"], revised[stage]["size_mm"])):
            raise RuntimeError(f"Yokai external bounds changed after parameter edit: {stage}")
    if not _same_signatures(revision["mount_holes"], operation["mount_holes"]):
        raise RuntimeError("Yokai mount-hole references changed after parameter edit")
    lower = _run(root, args.out, "lower-bound", "yokai_scale.py", freecad,
                 {"source": str(source), "recess_depth": 0.1}, args.require_adaptive)
    upper = _run(root, args.out, "upper-bound", "yokai_scale.py", freecad,
                 {"source": str(source), "recess_depth": 1.0}, args.require_adaptive)
    if not lower["measurement"]["volume_mm3"] > upper["measurement"]["volume_mm3"]:
        raise RuntimeError("Yokai depth boundaries did not produce ordered geometry")
    too_deep = _expect_failure(root, args.out, "too-deep", "yokai_scale.py", freecad,
                               {"source": str(source), "recess_depth": 1.01}, args.require_adaptive)
    if "0.1..1.0 mm" not in too_deep:
        raise RuntimeError("Yokai too-deep rejection did not report operation limit")
    if _digest(source) != source_digest:
        raise RuntimeError("Yokai source STEP changed during boundary checks")

    evidence = {
        "passed": True,
        "source": str(source),
        "source_sha256": source_digest,
        "require_adaptive": args.require_adaptive,
        "initial_result": str(args.out / "initial" / "result.json"),
        "revised_result": str(args.out / "revised" / "result.json"),
        "operation": operation,
        "revision": revision,
        "boundary_depths_mm": [0.1, 1.0],
        "too_deep_error": too_deep,
    }
    (args.out / "yokai-e2e.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "report": str(args.out / "yokai-e2e.json")}))


if __name__ == "__main__":
    main()
