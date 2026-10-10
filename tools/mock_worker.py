"""Headless Blender worker for the mock orchestrator.

  blender -b --python mock_worker.py -- motion <spec.json> <out.blend>

`motion` stands in for motion server + retarget: it makes object-level root
motion that follows the add-on's exported targets (inverting the canonical
transform), so the round trip of constraints can be checked by eye.
Passes use the real worker, blender_worker/passes.py.
"""

import json
import math
import os
import random
import sys

import bpy
from mathutils import Matrix, Vector


def canonical_to_world(start, scale, c):
    local = Vector((-c[0] / scale, c[2] / scale, c[1] / scale))
    return start @ local


def motion(spec_path, out_blend):
    with open(spec_path) as f:
        spec = json.load(f)
    frames = int(spec["frames"])
    rng = random.Random(spec.get("seed", 0))
    entries = []
    for cid in spec["char_ids"] or ["hero"]:
        cdata = spec["characters"].get(cid, {})
        start = Matrix(cdata["start_matrix_world"]) if cdata.get("start_matrix_world") else Matrix()
        scale = cdata.get("scale") or 1.0
        root2d = [c for c in cdata.get("constraints", []) if c["type"] == "root2d"]
        dense = next((c for c in root2d if len(c["frame_indices"]) >= frames), None)
        sparse = next((c for c in root2d if c is not dense), None)
        for v in range(int(spec.get("variants", 2))):
            sway = rng.uniform(0.02, 0.15)
            obj = bpy.data.objects.new(f"tmp_{cid}_{v}", None)
            bpy.context.scene.collection.objects.link(obj)
            for f in range(frames):
                t = f / max(1, frames - 1)
                if dense:
                    i = min(f, len(dense["smooth_root_2d"]) - 1)
                    x, z = dense["smooth_root_2d"][i]
                    cos_h, sin_h = dense["global_root_heading"][i]
                    head = math.atan2(sin_h, cos_h)
                elif sparse and sparse["frame_indices"]:
                    # piecewise linear from origin through the waypoints
                    keys = [(0, [0.0, 0.0])] + sorted(zip(sparse["frame_indices"], sparse["smooth_root_2d"]))
                    k = max(j for j, (kf, _) in enumerate(keys) if kf <= f)
                    (f0, a), (f1, b) = keys[k], keys[min(k + 1, len(keys) - 1)]
                    u = 0.0 if f1 == f0 else (f - f0) / (f1 - f0)
                    x, z = a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u
                    head = math.atan2(b[0] - a[0], b[1] - a[1]) if f1 != f0 else 0.0
                else:
                    x, z, head = 0.0, 3.0 * t, 0.0
                x += sway * math.sin(t * math.pi * 4)
                y = 0.04 * abs(math.sin(t * math.pi * 8))
                obj.location = canonical_to_world(start, scale, [x, y, z])
                start_yaw = start.to_euler().z
                obj.rotation_euler = (0.0, 0.0, start_yaw + head)
                obj.keyframe_insert("location", frame=f)
                obj.keyframe_insert("rotation_euler", frame=f)
            act = obj.animation_data.action
            act.name = f"mo_{cid}_{v}"
            act.use_fake_user = True
            entries.append({"char": cid, "action": act.name, "variant": v})
            bpy.data.objects.remove(obj)
    bpy.ops.wm.save_as_mainfile(filepath=out_blend)
    with open(os.path.join(os.path.dirname(out_blend), "actions.json"), "w") as f:
        json.dump(entries, f)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    if argv[0] == "motion":
        motion(argv[1], argv[2])
    else:
        raise SystemExit(f"unknown mode {argv[0]}")
    print("MOCK_WORKER_OK", flush=True)


main()
