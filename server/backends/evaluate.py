"""Eval: PCK@0.1 of the generated video vs the passes' kp2d.json."""

import hashlib
import json
import os
import subprocess
import sys

from .base import Backend, which

WORKER = os.path.join(os.path.dirname(__file__), "..", "workers", "pck_eval.py")


class DWPoseEval(Backend):
    kind, impl = "eval", "dwpose"

    @property
    def python(self):
        return os.path.expanduser(self.cfg.get("python") or sys.executable)

    def health(self):
        py = which(self.python)
        if not py:
            return f"missing python {self.python}"
        try:
            r = subprocess.run([py, "-c", "import rtmlib, cv2, onnxruntime"], capture_output=True, text=True,
                               timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            return f"error: {e}"
        if r.returncode != 0:
            return "missing rtmlib/opencv/onnxruntime in " + py
        return f"ok ({py}, {self.cfg.get('rtmlib_device', 'cpu')})"

    def evaluate(self, job, kp2d, video, chars, out_json, gpu_env):
        cmd = [self.python, os.path.abspath(WORKER), kp2d, video, out_json, *chars,
               "--device", self.cfg.get("rtmlib_device", "cpu")]
        job.run(cmd, env=gpu_env, timeout=self.cfg.get("timeout_s"), ok_marker="PCK_OK")
        with open(out_json) as f:
            return json.load(f)


class FakeEval(Backend):
    kind, impl = "eval", "fake"

    def health(self):
        return "fake"

    def evaluate(self, job, kp2d, video, chars, out_json, gpu_env):
        v = self.cfg.get("fake_value")
        if v is None:  # deterministic per video, in [0.4, 0.9)
            h = hashlib.sha256((video + str(os.path.getsize(video) if os.path.exists(video) else 0)).encode())
            v = 0.4 + 0.5 * int(h.hexdigest()[:8], 16) / 0x100000000
        res = {"pck@0.1": round(float(v), 3), "frames": 0, "per_char": {c: round(float(v), 3) for c in chars},
               "fake": True}
        with open(out_json, "w") as f:
            json.dump(res, f)
        return res


class NoEval(Backend):
    kind, impl = "eval", "none"

    def health(self):
        return "disabled"

    def evaluate(self, job, kp2d, video, chars, out_json, gpu_env):
        return None


IMPLS = {"dwpose": DWPoseEval, "fake": FakeEval, "none": NoEval}
