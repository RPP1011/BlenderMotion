"""Headless Blender backends: retarget (server/workers/retarget.py) and passes
(blender_worker/passes.py, unchanged)."""

import json
import os
import struct
import zlib

from ..config import blender_for
from .base import Backend, blender_version, which

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RETARGET_WORKER = os.path.join(ROOT, "server", "workers", "retarget.py")
PASSES_WORKER = os.path.join(ROOT, "blender_worker", "passes.py")


def _blender_health(exe):
    path = which(exe)
    if not path:
        return f"missing blender ({exe})"
    return f"ok ({blender_version(path) or path})"


class BlenderRetarget(Backend):
    kind, impl = "retarget", "blender"

    @property
    def exe(self):
        return blender_for(self.app.cfg, "retarget")

    def health(self):
        return _blender_health(self.exe)

    def retarget(self, job, spec):
        """spec: see server/workers/retarget.py. Returns the add-on artifact."""
        path = os.path.join(os.path.dirname(spec["out_blend"]), "retarget_spec.json")
        with open(path, "w") as f:
            json.dump(spec, f)
        job.run([which(self.exe) or self.exe, "-b", "--factory-startup", "--python", RETARGET_WORKER, "--", path],
                timeout=self.cfg.get("timeout_s"), ok_marker="RETARGET_OK")
        with open(spec["out_json"]) as f:
            return {"blend": spec["out_blend"], "actions": json.load(f)}


class FakeRetarget(Backend):
    kind, impl = "retarget", "fake"

    def health(self):
        return "fake (no .blend written)"

    def retarget(self, job, spec):
        entries = [{"char": cid, "action": f"mo_{cid}_{t['variant']}", "variant": t["variant"]}
                   for cid, c in spec["characters"].items() for t in c["takes"]]
        with open(spec["out_json"], "w") as f:
            json.dump(entries, f)
        with open(spec["out_blend"], "wb") as f:
            f.write(b"FAKE-BLEND")
        return {"blend": spec["out_blend"], "actions": entries}


class BlenderPasses(Backend):
    kind, impl = "passes", "blender"

    @property
    def exe(self):
        return blender_for(self.app.cfg, "passes")

    def health(self):
        return _blender_health(self.exe)

    def render(self, job, body, out_dir, gpu_env):
        spec = {**body, "out_dir": out_dir,
                "still_engine": body.get("still_engine") or self.cfg.get("still_engine", "EEVEE"),
                "playblast_engine": body.get("playblast_engine") or self.cfg.get("playblast_engine", "WORKBENCH"),
                "resolution_scale": body.get("resolution_scale") or self.cfg.get("resolution_scale", 100)}
        path = os.path.join(out_dir, "spec.json")
        with open(path, "w") as f:
            json.dump(spec, f)
        if not os.path.isfile(body.get("snapshot_path") or ""):
            raise RuntimeError(f"snapshot not found: {body.get('snapshot_path')}")
        job.run([which(self.exe) or self.exe, "-b", body["snapshot_path"], "--python", PASSES_WORKER, "--", path],
                env=gpu_env, timeout=self.cfg.get("timeout_s"), ok_marker="PASSES_OK")
        return passes_artifact(out_dir)


def passes_artifact(out_dir):
    return {"dir": out_dir, "playblast": os.path.join(out_dir, "playblast.mp4"),
            "first": os.path.join(out_dir, "first.png"), "last": os.path.join(out_dir, "last.png"),
            "kp2d": os.path.join(out_dir, "kp2d.json")}


def tiny_png(path, w=8, h=8, rgb=(128, 128, 128)):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(kind, data):
        c = struct.pack(">I", len(data)) + kind + data
        return c + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class FakePasses(Backend):
    """Placeholder files in the passes layout. The playblast is not a real
    movie: use the blender backend (it runs on CPU with Cycles) when the
    add-on needs to load it."""
    kind, impl = "passes", "fake"

    def health(self):
        return "fake (placeholder files)"

    def render(self, job, body, out_dir, gpu_env):
        if self.cfg.get("fake_delay_s"):
            job.sleep(float(self.cfg["fake_delay_s"]))
        n = int(body.get("frames_gen") or body.get("frames") or 5)
        res = body.get("resolution") or [64, 64]
        chars = list((body.get("characters") or {"hero": ""}).keys())
        frames = [{"i": i, "scene_frame": int(body.get("frame_in", 1)) + i,
                   "people": {c: [None] + [[res[0] * (0.4 + 0.01 * j), res[1] * (0.2 + 0.04 * j), 1]
                                           for j in range(13)] + [None] * 4 for c in chars}}
                  for i in range(n)]
        with open(os.path.join(out_dir, "kp2d.json"), "w") as f:
            json.dump({"format": "coco18", "resolution": res, "fps": 24, "frame_in": body.get("frame_in", 1),
                       "frame_out": body.get("frame_out", n + 1), "frames_held": 0, "frames": frames}, f)
        tiny_png(os.path.join(out_dir, "first.png"))
        tiny_png(os.path.join(out_dir, "last.png"))
        with open(os.path.join(out_dir, "playblast.mp4"), "wb") as f:
            f.write(b"FAKE-MP4")
        return passes_artifact(out_dir)


RETARGET_IMPLS = {"blender": BlenderRetarget, "fake": FakeRetarget}
PASSES_IMPLS = {"blender": BlenderPasses, "fake": FakePasses}
