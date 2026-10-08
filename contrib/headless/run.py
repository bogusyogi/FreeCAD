# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run a trusted Python model in an isolated FreeCAD command-line process."""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys


def failure(code, message, paths):
    report = {"ok": False, "error": message, "error_code": code}
    report.update({key: str(paths[key]) for key in ("model", "freecad", "out")})
    return report


def finish(report, result_path=None):
    if result_path is not None:
        try:
            result_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError:
            pass
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") is True else 1


def preflight(args):
    paths = {"model": args.model, "freecad": args.freecad, "out": args.out}
    resolved = {}
    for key, code, label in (("model", "missing_model", "Model"),
                             ("freecad", "missing_runtime", "FreeCAD runtime")):
        try:
            value = Path(paths[key]).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            return None, failure(code, f"{label} could not be resolved: {exc}", paths)
        valid = value.is_file() and (key == "model" or os.access(value, os.X_OK))
        if not valid:
            code = "invalid_model" if key == "model" else "invalid_runtime"
            message = "Model path must be a regular file" if key == "model" else \
                "FreeCAD path must be an executable file"
            return None, failure(code, message, {**paths, key: value})
        resolved[key] = value
    try:
        expanded_out = Path(paths["out"]).expanduser()
        if os.path.lexists(expanded_out):
            return None, failure("output_exists", "Output directory must not already exist",
                                 {**paths, "out": expanded_out})
        resolved["out"] = expanded_out.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, failure("invalid_output", f"Output directory could not be resolved: {exc}", paths)
    worker = Path(__file__).with_name("worker.py")
    if not worker.is_file():
        return None, failure("missing_worker", f"Runner worker is missing: {worker}", paths)
    return resolved, None


def read_worker_result(path):
    try:
        if not path.exists():
            return None, "result.json was not produced"
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        return None, f"result.json is not valid JSON: {exc}"
    if not isinstance(report, dict) or type(report.get("ok")) is not bool:
        return None, "result.json must be a JSON object with boolean 'ok'"
    return report, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path, help="Python file exposing build(params)")
    parser.add_argument("--freecad", required=True, type=Path, help="FreeCADCmd executable")
    parser.add_argument("--out", required=True, type=Path, help="New output directory")
    parser.add_argument("--params", default="{}", help="JSON parameter object")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--require-adaptive", action="store_true",
                        help="Reject runtimes without the fork's adaptive volume API")
    args = parser.parse_args()
    paths = {"model": args.model, "freecad": args.freecad, "out": args.out}
    try:
        params = json.loads(args.params)
    except (TypeError, ValueError, RecursionError, json.JSONDecodeError) as exc:
        return finish(failure("invalid_params", f"--params must be a valid JSON object: {exc}", paths))
    if not isinstance(params, dict):
        return finish(failure("invalid_params", "--params must be a valid JSON object", paths))
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600:
        return finish(failure("invalid_timeout", "--timeout must be finite and in (0, 3600]", paths))
    resolved, error = preflight(args)
    if error:
        return finish(error)
    paths = resolved
    try:
        paths["out"].mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return finish(failure("output_create_failed", f"Could not create output directory: {exc}", paths))
    request_path = paths["out"] / "request.json"
    result_path = paths["out"] / "result.json"
    request = {"model": str(paths["model"]), "params": params, "out": str(paths["out"]),
               "require_adaptive": args.require_adaptive}
    try:
        request_path.write_text(json.dumps(request), encoding="utf-8")
    except OSError as exc:
        return finish(failure("request_write_failed", f"Could not write request.json: {exc}", paths), result_path)
    env = os.environ.copy()
    env["FREECAD_MODEL_REQUEST"] = str(request_path)
    try:
        log_file = (paths["out"] / "freecad.log").open("w", encoding="utf-8")
    except OSError as exc:
        return finish(failure("log_open_failed", f"Could not create freecad.log: {exc}", paths), result_path)
    runtime_error = runtime_code = None
    with log_file as log:
        try:
            process = subprocess.run(
                [str(paths["freecad"]), str(Path(__file__).with_name("worker.py"))],
                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                timeout=args.timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                check=False)
            if process.returncode:
                runtime_error, runtime_code = f"FreeCAD exited with status {process.returncode}", "runtime_exit"
        except subprocess.TimeoutExpired:
            runtime_error, runtime_code = f"FreeCAD timed out after {args.timeout:g} seconds", "runtime_timeout"
        except OSError as exc:
            runtime_error, runtime_code = f"Could not start FreeCAD runtime: {exc}", "runtime_launch_failed"
    worker_report, result_error = read_worker_result(result_path)
    if worker_report is not None:
        if worker_report["ok"] and (runtime_error or not all((paths["out"] / name).is_file()
                                                              for name in ("model.FCStd", "model.step"))):
            result_error = "successful result.json is missing required output files"
            worker_report = None
        else:
            for key in ("model", "freecad", "out"):
                worker_report.setdefault(key, str(paths[key]))
            return finish(worker_report)
    report = failure(runtime_code or "malformed_worker_result",
                     runtime_error or f"FreeCAD produced an invalid worker result: {result_error}", paths)
    return finish(report, result_path)


if __name__ == "__main__":
    sys.exit(main())
