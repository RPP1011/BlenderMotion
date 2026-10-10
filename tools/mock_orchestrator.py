#!/usr/bin/env python3
"""Stand-in orchestrator implementing the add-on's API (docs/architecture.md §6).

No models. Stdlib only. Exists so the add-on can be developed and tested
end to end before the real orchestrator, motion server and ComfyUI exist.

    python tools/mock_orchestrator.py --blender /path/to/blender [--port 8765]

With --blender, motion and passes jobs run real headless Blender workers
(tools/mock_worker.py): motion = procedural root motion that follows the
authored targets; passes = a 1-sample Cycles playblast. "generate" copies the
playblast as the output video and reports a random PCK. Without --blender,
motion/passes jobs fail with a clear message; plan/shots/health still work.
"""

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "mock_worker.py")

STATE = {"projects": {}, "jobs": {}}
LOCK = threading.Lock()
ARGS = None


def project(name):
    with LOCK:
        return STATE["projects"].setdefault(name, {
            "root": os.path.abspath(os.path.join(ARGS.data, name)), "meta": {}, "shots": {}, "stages": {},
        })


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# -- jobs ----------------------------------------------------------------------

class Cancelled(Exception):
    pass


def new_job(proj, shot, stage, fn):
    job = {"id": "job_" + uuid.uuid4().hex[:10], "project": proj, "shot": shot, "stage": stage,
           "status": "queued", "progress": 0.0, "artifact": {}, "error": "", "logs": [],
           "cancel": False}
    with LOCK:
        STATE["jobs"][job["id"]] = job
    threading.Thread(target=_run_job, args=(job, fn), daemon=True).start()
    return job


def _run_job(job, fn):
    try:
        time.sleep(0.3)
        job["status"] = "running"
        fn(job)
        job["status"] = "done"
        job["progress"] = 1.0
    except Cancelled:
        job["status"] = "cancelled"
    except Exception as e:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(e)
    p = project(job["project"])
    p["stages"].setdefault(job["shot"], {})[job["stage"]] = {"status": job["status"], "job_id": job["id"]}


def step(job, stage, progress, delay=0.2):
    if job["cancel"]:
        raise Cancelled()
    job["stage"] = stage
    job["progress"] = progress
    time.sleep(delay * ARGS.speed)


def run_blender(job, args):
    if not ARGS.blender:
        raise RuntimeError("mock orchestrator started without --blender; cannot run workers")
    cmd = [ARGS.blender, "-b", "--factory-startup", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    job["logs"] = (proc.stdout + proc.stderr).splitlines()[-40:]
    if proc.returncode != 0 or "MOCK_WORKER_OK" not in proc.stdout:
        raise RuntimeError("worker failed: " + " | ".join(job["logs"][-5:]))


# -- stage implementations -------------------------------------------------------

def do_motion(job, proj, shot_id, body):
    p = project(proj)
    shot = p["shots"].get(shot_id, {})
    for i in range(4):
        step(job, "motion", 0.1 + 0.1 * i)
    out_dir = os.path.join(p["root"], "cas", "motion_" + job["id"])
    os.makedirs(out_dir, exist_ok=True)
    spec = {
        "characters": body.get("characters", {}),
        "frames": body.get("frames", round(shot.get("duration_s", 4) * shot.get("fps", 24))),
        "variants": shot.get("motion", {}).get("variants", 2),
        "seed": shot.get("motion", {}).get("seed", 0),
        "char_ids": [c["char_id"] for c in shot.get("characters", [])],
    }
    spec_path = os.path.join(out_dir, "spec.json")
    write_json(spec_path, spec)
    blend = os.path.join(out_dir, "motion.blend")
    step(job, "retarget", 0.6, 0)
    run_blender(job, ["--python", WORKER, "--", "motion", spec_path, blend])
    with open(os.path.join(out_dir, "actions.json")) as f:
        job["artifact"] = {"blend": blend, "actions": json.load(f)}


def do_passes(job, proj, shot_id, body):
    p = project(proj)
    out_dir = os.path.join(p["root"], "cas", "passes_" + body.get("scene_hash", job["id"])[:16])
    os.makedirs(out_dir, exist_ok=True)
    step(job, "passes", 0.2)
    spec_path = os.path.join(out_dir, "spec.json")
    write_json(spec_path, {**body, "out_dir": out_dir})
    run_blender(job, [body["snapshot_path"], "--python", WORKER, "--", "passes", spec_path])
    job["artifact"] = {"dir": out_dir, "playblast": os.path.join(out_dir, "playblast.mp4"),
                       "kp2d": os.path.join(out_dir, "kp2d.json")}


def do_generate(job, proj, shot_id, body):
    p = project(proj)
    passes = p["stages"].get(shot_id, {}).get("passes_artifact")
    if not passes:
        raise RuntimeError("no passes for this shot")
    for i in range(5):
        step(job, "generate", 0.1 + 0.15 * i)
    out = os.path.join(p["root"], "cas", f"gen_{job['id']}.mp4")
    shutil.copyfile(passes["playblast"], out)
    job["artifact"] = {"video": out}
    step(job, "eval", 0.9)
    job["artifact"] = {"video": out, "pck": round(random.uniform(0.4, 0.9), 3)}


# -- HTTP ----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        if ARGS.verbose:
            sys.stderr.write("mock: " + fmt % a + "\n")

    def _send(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            return self._send(200, {"planner": "mock", "motion": "mock", "comfy": "mock",
                                    "blender": "yes" if ARGS.blender else "no"})
        m = re.fullmatch(r"/jobs/([^/]+)", path)
        if m:
            job = STATE["jobs"].get(m.group(1))
            if not job:
                return self._send(404, {"error": "no such job"})
            return self._send(200, {k: v for k, v in job.items() if k not in ("cancel", "logs")}
                              | {"logs_tail": job["logs"][-10:]})
        m = re.fullmatch(r"/projects/([^/]+)/shots/([^/]+)", path)
        if m:
            p = project(m.group(1))
            shot = p["shots"].get(m.group(2))
            if shot is None:
                return self._send(404, {"error": "no such shot"})
            stages = {k: v for k, v in p["stages"].get(m.group(2), {}).items() if isinstance(v, dict)
                      and "status" in v}
            return self._send(200, {"shot": shot, "stages": stages})
        return self._send(404, {"error": "not found"})

    def do_PUT(self):
        body = self._body()
        m = re.fullmatch(r"/projects/([^/]+)", self.path)
        if m:
            p = project(m.group(1))
            if body.get("root"):
                p["root"] = body["root"]
            p["meta"] = body
            write_json(os.path.join(p["root"], "project.json"), body)
            return self._send(200, {"ok": True})
        m = re.fullmatch(r"/projects/([^/]+)/shots/([^/]+)", self.path)
        if m:
            p = project(m.group(1))
            p["shots"][m.group(2)] = body
            write_json(os.path.join(p["root"], "shots", m.group(2) + ".json"), body)
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self._body()
        m = re.fullmatch(r"/jobs/([^/]+)/cancel", self.path)
        if m:
            job = STATE["jobs"].get(m.group(1))
            if not job:
                return self._send(404, {"error": "no such job"})
            job["cancel"] = True
            return self._send(200, {"ok": True})
        m = re.fullmatch(r"/projects/([^/]+)/plan", self.path)
        if m:
            beats = [s.strip() for s in re.split(r"[.\n]+", body.get("brief", "")) if len(s.strip()) > 8]
            chars = body.get("characters") or [{"char_id": "hero", "rig": ""}]
            shots = [{
                "id": f"sh_{(i + 1) * 10:03d}", "order": (i + 1) * 10, "duration_s": 4.0,
                "characters": [{"char_id": c["char_id"], "rig": c.get("rig", ""), "refs": [], "lora": None}
                               for c in chars[:1]],
                "action_prompt": beat, "motion": {"backend": "kimodo", "seed": 1000 + i, "variants": 2},
                "camera": {"mode": "authored"}, "look": {"prompt": ""},
            } for i, beat in enumerate(beats[:8])]
            return self._send(200, {"shots": shots})
        m = re.fullmatch(r"/projects/([^/]+)/shots/([^/]+)/(motion|passes|generate)", self.path)
        if m:
            proj, shot_id, kind = m.groups()
            if shot_id not in project(proj)["shots"]:
                return self._send(404, {"error": "PUT the shot first"})
            fn = {"motion": do_motion, "passes": do_passes, "generate": do_generate}[kind]

            def run(job, fn=fn):
                fn(job, proj, shot_id, body)
                if kind == "passes":
                    project(proj)["stages"].setdefault(shot_id, {})["passes_artifact"] = job["artifact"]

            job = new_job(proj, shot_id, kind, run)
            return self._send(202, {"job_id": job["id"], "status": job["status"]})
        return self._send(404, {"error": "not found"})


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--blender", default=os.environ.get("BLENDER"), help="Blender executable for workers")
    ap.add_argument("--data", default=os.path.join(os.getcwd(), "mock_data"),
                    help="Fallback project root parent when the add-on sends none")
    ap.add_argument("--speed", type=float, default=1.0, help="Multiplier on simulated delays")
    ap.add_argument("-v", "--verbose", action="store_true")
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    print(f"mock orchestrator on http://127.0.0.1:{ARGS.port} (blender: {ARGS.blender or 'none'})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
