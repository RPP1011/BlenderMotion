"""Motion backends: text (+ root2d constraints) -> Kimodo takes (.npz + .bvh).

`generate(job, req, env)` gets
  {char_id, prompt, total_s, frames_30, seed, num_samples, constraints, out_dir}
(`constraints` already in Kimodo's format, at 30 fps) and returns
[{"npz": path, "bvh": path}] in sample order. Ranking happens in the pipeline.
"""

import json
import os
import re

from .. import fakemotion, kimodo
from .base import Backend, which

_TQDM = re.compile(r"(\d{1,3})%\|")


class KimodoMotion(Backend):
    kind, impl = "motion", "kimodo"

    def _bin(self):
        venv = os.path.expanduser(self.cfg.get("venv") or "")
        cmd = list(self.cfg.get("command") or ["kimodo_gen"])
        exe = cmd[0]
        if venv and not os.path.isabs(exe):
            cand = os.path.join(venv, "bin", exe)
            if os.path.exists(cand):
                cmd[0] = cand
        return cmd, venv

    def _env(self, gpu_env):
        cmd, venv = self._bin()
        env = {}
        if venv:
            env["VIRTUAL_ENV"] = venv
            env["PATH"] = os.path.join(venv, "bin") + os.pathsep + os.environ.get("PATH", "")
        env.update(self.cfg.get("env") or {})
        env.update(gpu_env or {})
        return env

    def health(self):
        cmd, venv = self._bin()
        if venv and not os.path.isdir(venv):
            return f"missing venv {venv}"
        found = which(cmd[0])
        if not found:
            return f"missing {cmd[0]}" + (f" (venv {venv})" if venv else "")
        return f"ok ({found}, {self.cfg.get('device') or 'default GPU'})"

    def generate(self, job, req, gpu_env):
        os.makedirs(req["out_dir"], exist_ok=True)
        cons_path = None
        if req["constraints"]:
            cons_path = os.path.join(req["out_dir"], f"{req['char_id']}_constraints.json")
            with open(cons_path, "w") as f:
                json.dump(req["constraints"], f)
        stem = os.path.join(req["out_dir"], f"{req['char_id']}_s{req['seed']}")
        cmd, _ = self._bin()
        cmd = kimodo.build_command(cmd, req["prompt"], req["total_s"], req["seed"], req["num_samples"],
                                   stem, cons_path, self.cfg.get("extra_args") or ())
        base = job.get("progress") or 0.0

        def on_line(line):
            m = _TQDM.search(line)
            if m:
                job.set_progress(base + (req.get("progress_span", 0.5)) * int(m.group(1)) / 100)

        job.run(cmd, env=self._env(gpu_env), cwd=self.cfg.get("cwd") or req["out_dir"],
                timeout=self.cfg.get("timeout_s"), on_line=on_line)
        pairs = kimodo.discover_outputs(stem)
        if not pairs:
            raise RuntimeError(f"Kimodo wrote no .npz/.bvh next to {stem}")
        out = [{"npz": n, "bvh": b} for n, b in pairs]
        # One batched .npz with K bvh files: give every take the same npz plus its index.
        if len(out) > 1 and len({t["npz"] for t in out if t["npz"]}) == 1:
            for k, t in enumerate(out):
                t["npz"], t["index"] = out[0]["npz"], k
        return out


class FakeMotion(Backend):
    kind, impl = "motion", "fake"

    def health(self):
        return "fake (synthetic SOMA walk)"

    def generate(self, job, req, gpu_env):
        os.makedirs(req["out_dir"], exist_ok=True)
        floating = set(self.cfg.get("fake_floating") or [])
        out = []
        for k in range(int(req["num_samples"])):
            job.check_cancel()
            if self.cfg.get("fake_delay_s"):
                job.sleep(float(self.cfg["fake_delay_s"]))
            stem = os.path.join(req["out_dir"], f"{req['char_id']}_s{req['seed']}_{k}")
            npz, bvh = fakemotion.write_take(stem, req["frames_30"], req["constraints"], req["seed"] * 31 + k,
                                             floating=k in floating)
            out.append({"npz": npz, "bvh": bvh})
        return out


IMPLS = {"kimodo": KimodoMotion, "fake": FakeMotion}
