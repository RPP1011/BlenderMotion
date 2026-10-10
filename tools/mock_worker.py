"""Headless Blender worker for the mock orchestrator.

  blender -b --python mock_worker.py -- motion <spec.json> <out.blend>
  blender -b <snapshot.blend> --python mock_worker.py -- passes <spec.json>

`motion` stands in for motion server + retarget: it makes object-level root
motion that follows the add-on's exported targets (inverting the canonical
transform), so the round trip of constraints can be checked by eye.
`passes` renders a low-res 1-sample Cycles playblast of the shot through its camera.
It is not the real passes worker (no kp2d, depth or line art).
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
        dense = next((c for c in cdata.get("constraints", []) if c["type"] == "root2d"
                      and c["mode"] == "dense"), None)
        sparse = next((c for c in cdata.get("constraints", []) if c["type"] == "root2d"
                       and c["mode"] == "sparse"), None)
        for v in range(int(spec.get("variants", 2))):
            sway = rng.uniform(0.02, 0.15)
            obj = bpy.data.objects.new(f"tmp_{cid}_{v}", None)
            bpy.context.scene.collection.objects.link(obj)
            for f in range(frames):
                t = f / max(1, frames - 1)
                if dense:
                    i = min(f, len(dense["xz"]) - 1)
                    x, z = dense["xz"][i]
                    head = dense["heading"][i] or 0.0
                elif sparse and sparse["frames"]:
                    # piecewise linear from origin through the waypoints
                    keys = [(0, [0.0, 0.0])] + sorted(zip(sparse["frames"], sparse["xz"]))
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


def passes(spec_path):
    with open(spec_path) as f:
        spec = json.load(f)
    scene = bpy.data.scenes[spec["scene"]]
    scene.camera = bpy.data.objects[spec["camera"]]
    scene.frame_start = spec["frame_in"]
    scene.frame_end = spec["frame_out"] - 1
    scene.render.use_sequencer = False
    scene.render.use_compositing = False
    # Cycles on CPU: Workbench/EEVEE need a GPU context (EGL), which headless
    # CI boxes often lack. One sample is plenty for a review playblast.
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.render.resolution_percentage = 25
    img = scene.render.image_settings
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    # first.png: the in-frame as a still (H3 first/last-frame input; shot card thumbnail).
    scene.frame_set(spec["frame_in"])
    if hasattr(img, "media_type"):
        img.media_type = "IMAGE"
    img.file_format = "PNG"
    scene.render.filepath = os.path.join(spec["out_dir"], "first.png")
    bpy.ops.render.render(write_still=True, scene=scene.name)
    if hasattr(img, "media_type"):
        img.media_type = "VIDEO"
    img.file_format = "FFMPEG"
    scene.render.filepath = os.path.join(spec["out_dir"], "playblast.mp4")
    scene.render.use_file_extension = False
    bpy.ops.render.render(animation=True, scene=scene.name)
    with open(os.path.join(spec["out_dir"], "kp2d.json"), "w") as f:
        json.dump({"note": "mock worker: no keypoints", "frames": []}, f)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    if argv[0] == "motion":
        motion(argv[1], argv[2])
    elif argv[0] == "passes":
        passes(argv[1])
    else:
        raise SystemExit(f"unknown mode {argv[0]}")
    print("MOCK_WORKER_OK", flush=True)


main()
