# SPDX-License-Identifier: LGPL-2.1-or-later
"""Check an export_gltf.py GLB & metadata pair without extra packages."""

import argparse
import json
import math
from pathlib import Path

from export_gltf import read_glb


def expected_children(record):
    return [child["label"] for child in record.get("children", []) if child.get("visible", True)]


def expected_translation(placement):
    x, y, z = placement["translation"]
    return [x / 1000, z / 1000, -y / 1000]


def expected_rotation(placement):
    x, y, z, w = placement["rotation_quaternion_xyzw"]
    root_half = math.sqrt(0.5)
    # Conjugate FreeCAD's XYZ rotation by the Z-up -> glTF Y-up basis rotation.
    basis = (-root_half, 0.0, 0.0, root_half)
    inv_basis = (root_half, 0.0, 0.0, root_half)

    def multiply(a, b):
        ax, ay, az, aw = a
        bx, by, bz, bw = b
        return (aw * bx + ax * bw + ay * bz - az * by,
                aw * by - ax * bz + ay * bw + az * bx,
                aw * bz + ax * by - ay * bx + az * bw,
                aw * bw - ax * bx - ay * by - az * bz)

    return multiply(multiply(basis, (x, y, z, w)), inv_basis)


def assert_node_tree(gltf, metadata):
    nodes = gltf["nodes"]
    scenes = gltf.get("scenes", [])
    if len(scenes) != 1 or len(scenes[0].get("nodes", [])) != 1:
        raise AssertionError("expected one named assembly root in the GLB scene")

    def visit(node_index, record):
        node = nodes[node_index]
        if node.get("name") != record["label"]:
            raise AssertionError(f"node label changed: expected {record['label']!r}, got {node.get('name')!r}")
        children = node.get("children", [])
        expected = expected_children(record)
        actual = [nodes[index].get("name") for index in children]
        if actual != expected:
            raise AssertionError(f"{record['label']}: expected children {expected}, got {actual}")
        translation = node.get("translation", [0.0, 0.0, 0.0])
        expected_t = expected_translation(record["placement_mm"])
        if any(abs(a - b) > 1e-7 for a, b in zip(translation, expected_t)):
            raise AssertionError(f"{record['label']}: GLB translation does not match FCStd placement")
        rotation = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
        expected_q = expected_rotation(record["placement_mm"])
        if min(max(abs(a - b) for a, b in zip(rotation, expected_q)),
               max(abs(a + b) for a, b in zip(rotation, expected_q))) > 1e-6:
            raise AssertionError(f"{record['label']}: GLB rotation does not match FCStd placement")
        records = [child for child in record.get("children", []) if child.get("visible", True)]
        for index, child in zip(children, records):
            visit(index, child)
        if record.get("geometry"):
            if "mesh" not in node:
                raise AssertionError(f"shaped leaf {record['label']} has no GLB mesh")
            mesh = gltf["meshes"][node["mesh"]]
            if not mesh.get("primitives") or any("material" not in p for p in mesh["primitives"]):
                raise AssertionError(f"shaped leaf {record['label']} has no assigned visible material")

    visit(scenes[0]["nodes"][0], metadata["assembly"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("glb", type=Path)
    parser.add_argument("metadata", type=Path)
    args = parser.parse_args()
    _, gltf, _ = read_glb(args.glb)
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    assert_node_tree(gltf, metadata)
    if metadata["units"]["source_length"] != "mm" or metadata["units"]["glb_length"] != "m":
        raise AssertionError("length unit metadata is missing or incorrect")
    if metadata["measurement"]["solids"] < 1:
        raise AssertionError("export metadata reports no solids")
    print(json.dumps({"ok": True, "mesh_nodes": metadata["glb"]["mesh_nodes"],
                      "materials": metadata["glb"]["material_count"]}))


if __name__ == "__main__":
    main()
