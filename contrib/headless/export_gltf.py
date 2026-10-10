# SPDX-License-Identifier: LGPL-2.1-or-later
"""Export a saved FCStd assembly to GLB in a real FreeCADCmd process.

The FreeCAD OCCT writer preserves native node names and placements, but needs
triangulations to exist on every BRep. This driver validates the written GLB
and records measured assembly metadata next to it.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_glb(path):
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("FreeCAD output is not a binary GLB")
    version = int.from_bytes(data[4:8], "little")
    declared_length = int.from_bytes(data[8:12], "little")
    json_length = int.from_bytes(data[12:16], "little")
    chunk_type = int.from_bytes(data[16:20], "little")
    if version != 2 or declared_length != len(data) or chunk_type != 0x4E4F534A:
        raise ValueError("Malformed GLB header or JSON chunk")
    return data, json.loads(data[20:20 + json_length].decode("utf-8").rstrip(" \0")), data[20 + json_length:]


def write_glb(path, document, binary_chunk):
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded += b" " * ((-len(encoded)) % 4)
    total = 12 + 8 + len(encoded) + len(binary_chunk)
    path.write_bytes(b"glTF" + (2).to_bytes(4, "little") + total.to_bytes(4, "little")
                     + len(encoded).to_bytes(4, "little") + (0x4E4F534A).to_bytes(4, "little")
                     + encoded + binary_chunk)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path, help="Saved FreeCAD .FCStd document")
    parser.add_argument("--freecad", required=True, type=Path, help="FreeCADCmd executable")
    parser.add_argument("--out", required=True, type=Path, help="Output .glb path")
    parser.add_argument("--metadata", type=Path, help="Metadata JSON path (default: beside GLB)")
    parser.add_argument("--color-map", type=Path,
                        help="Optional JSON with label-to-sRGB material colors & provenance")
    parser.add_argument("--source-step", type=Path,
                        help="Optional originating STEP file to hash into output provenance")
    args = parser.parse_args()
    source = args.document.expanduser().resolve(strict=True)
    freecad = args.freecad.expanduser().resolve(strict=True)
    output = args.out.expanduser().resolve()
    metadata = (args.metadata or output.with_suffix(".metadata.json")).expanduser().resolve()
    colors = args.color_map.expanduser().resolve(strict=True) if args.color_map else None
    source_step = args.source_step.expanduser().resolve(strict=True) if args.source_step else None
    if source.suffix.lower() != ".fcstd":
        parser.error("document must have .FCStd extension")
    if output.suffix.lower() != ".glb":
        parser.error("--out must have .glb extension")
    if output.exists() or metadata.exists():
        parser.error("GLB and metadata outputs must not already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    request = {"document": str(source), "output": str(output), "metadata": str(metadata),
               "color_map": str(colors) if colors else None,
               "source_step": str(source_step) if source_step else None}
    with tempfile.TemporaryDirectory(prefix="freecad-gltf-") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        result_path = Path(temp_dir) / "result.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        env = os.environ.copy()
        env["FREECAD_GLTF_REQUEST"] = str(request_path)
        process = subprocess.run([str(freecad), str(Path(__file__).with_name("export_gltf_worker.py"))],
                                 env=env, stdin=subprocess.DEVNULL, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 timeout=300,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if process.returncode != 0 or not result_path.is_file():
            print(process.stdout, file=sys.stderr)
            raise SystemExit(f"FreeCAD export failed (exit {process.returncode})")
        report = json.loads(result_path.read_text(encoding="utf-8"))
    _, gltf, binary = read_glb(output)
    material_ids = {}
    color_table = report.pop("color_table")
    for label, color in color_table.items():
        key = tuple(round(float(channel), 6) for channel in color["rgba_linear"])
        if key not in material_ids:
            material_ids[key] = len(gltf.setdefault("materials", []))
            gltf["materials"].append({
                "name": color["name"],
                "pbrMetallicRoughness": {"baseColorFactor": list(key),
                                         "metallicFactor": 0.0, "roughnessFactor": 0.72},
                "doubleSided": True,
            })
        material = material_ids[key]
        for node in gltf.get("nodes", []):
            if node.get("name") == label and "mesh" in node:
                for primitive in gltf["meshes"][node["mesh"]].get("primitives", []):
                    primitive["material"] = material
    node_names = [node.get("name") for node in gltf.get("nodes", [])]
    expected_meshes = set(report["shape_labels"])
    actual_meshes = {node.get("name") for node in gltf.get("nodes", []) if "mesh" in node}
    missing = expected_meshes - actual_meshes
    if missing:
        raise RuntimeError(f"GLB lost shape labels: {sorted(missing)}")
    report["glb"] = {"path": str(output), "bytes": output.stat().st_size,
                      "sha256": sha256(output), "nodes": len(node_names),
                      "mesh_nodes": len(actual_meshes), "material_count": len(gltf.get("materials", [])),
                      "mesh_labels": sorted(actual_meshes)}
    write_glb(output, gltf, binary)
    report["glb"]["bytes"] = output.stat().st_size
    report["glb"]["sha256"] = sha256(output)
    metadata.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "glb": str(output), "metadata": str(metadata),
                      "mesh_nodes": len(actual_meshes), "materials": report["glb"]["material_count"]}))


if __name__ == "__main__":
    main()
