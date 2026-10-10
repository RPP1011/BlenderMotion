#!/usr/bin/env python3
"""Self-hosted orchestrator for the chimera_director add-on.

Implements the add-on's HTTP contract (docs/addon.md "Orchestrator contract")
with local backends: Kimodo (motion), headless Blender (retarget, passes),
ComfyUI + MiniMax H3 (generate), DWPose PCK (eval). Every backend has a
`fake` implementation, so the server also runs with no GPU.

    python -m server.orchestrator --config server/config.example.json
    python -m server.orchestrator --fake --blender /path/to/blender   # no models

See server/README.md.
"""

import argparse
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):  # run as a script: python server/orchestrator.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import backends, config, pipeline  # noqa: E402
from server.jobs import TERMINAL, Cancelled, GpuPool, JobStore  # noqa: E402
from server.state import Projects  # noqa: E402

RUNNERS = {"motion": pipeline.run_motion, "passes": pipeline.run_passes, "generate": pipeline.run_generate}
HEALTH_EVERY_S = 15.0


class Orchestrator:
    def __init__(self, cfg):
        self.cfg = cfg
        os.makedirs(cfg["data_dir"], exist_ok=True)
        self.projects = Projects(cfg["data_dir"])
        self.gpus = GpuPool(cfg.get("gpus"))
        self.plan = backends.make("plan", cfg, self)
        self.motion = backends.make("motion", cfg, self)
        self.retarget = backends.make("retarget", cfg, self)
        self.passes = backends.make("passes", cfg, self)
        self.generate = backends.make("generate", cfg, self)
        self.evaluate = backends.make("eval", cfg, self)
        self._last_seen = {}
        self.jobs = JobStore(cfg["data_dir"], on_change=self._job_changed)
        self._health = {name: "checking" for name in self._backends()}
        self._health_stop = threading.Event()

    def _backends(self):
        return {"plan": self.plan, "motion": self.motion, "retarget": self.retarget, "passes": self.passes,
                "generate": self.generate, "eval": self.evaluate}

    def use_cache(self, body):
        return bool(self.cfg.get("cache", True)) and not (body or {}).get("force")

    # -- health ----------------------------------------------------------------------
    def refresh_health(self):
        out = {}
        for name, b in self._backends().items():
            try:
                out[name] = f"{b.impl}: {b.health()}"
            except Exception as e:  # noqa: BLE001
                out[name] = f"{b.impl}: error {e!r}"
        self._health = out
        return out

    def start_health(self):
        def loop():
            while not self._health_stop.is_set():
                self.refresh_health()
                self._health_stop.wait(HEALTH_EVERY_S)
        threading.Thread(target=loop, name="health", daemon=True).start()

    def health(self):
        out = dict(self._health)
        for name, st in self.gpus.status().items():
            out["gpu " + name] = st
        counts = self.jobs.counts()
        out["jobs"] = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "idle"
        return out

    # -- jobs ------------------------------------------------------------------------
    def _job_changed(self, job):
        d = job.data
        sig = (d["stage"], d["status"])
        if self._last_seen.get(d["id"]) == sig:
            return
        self._last_seen[d["id"]] = sig
        if self.projects.known(d["project"]):
            try:
                self.projects.get(d["project"]).record_job(d)
            except OSError:
                pass

    def submit(self, project, shot_id, kind, body):
        proj = self.projects.get(project)
        job = self.jobs.new(project, shot_id, kind)
        job.log(f"{kind} job for {project}/{shot_id}")
        threading.Thread(target=self._run, args=(job, proj, shot_id, body), name=job.id, daemon=True).start()
        return job

    def _run(self, job, proj, shot_id, body):
        try:
            RUNNERS[job.get("kind")](self, job, proj, shot_id, body)
            job.check_cancel()
            job.update(status="done", progress=1.0)
        except Cancelled:
            job.log("cancelled")
            job.update(status="cancelled")
        except Exception as e:  # noqa: BLE001
            job.log(traceback.format_exc())
            job.update(status="failed", error=str(e)[:2000])
        finally:
            self._last_seen.pop(job.id, None)

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        return job.cancel()


# -- HTTP ----------------------------------------------------------------------------

def make_handler(app, verbose=False):
    class Handler(BaseHTTPRequestHandler):

        def log_message(self, fmt, *a):
            if verbose:
                sys.stderr.write("orchestrator: " + fmt % a + "\n")

        def _send(self, code, data):
            raw = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            data = json.loads(self.rfile.read(n))
            if not isinstance(data, dict):
                raise ValueError("body must be a JSON object")
            return data

        def _path(self):
            return [urllib.parse.unquote(p) for p in urllib.parse.urlparse(self.path).path.strip("/").split("/")]

        def _dispatch(self, method):
            try:
                body = self._body() if method in ("PUT", "POST") else {}
            except ValueError as e:
                return self._send(400, {"error": f"bad JSON body: {e}"})
            try:
                return self._route(method, self._path(), body)
            except (ValueError, KeyError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(500, {"error": repr(e)})

        def do_GET(self):
            self._dispatch("GET")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_POST(self):
            self._dispatch("POST")

        def _route(self, method, parts, body):
            if method == "GET" and parts == ["health"]:
                return self._send(200, app.health())
            if parts[:1] == ["jobs"]:
                return self._jobs(method, parts[1:])
            if parts[:1] == ["projects"] and len(parts) >= 2:
                return self._projects(method, parts[1], parts[2:], body)
            return self._send(404, {"error": "not found"})

        def _jobs(self, method, rest):
            if method == "GET" and not rest:
                jobs = sorted(app.jobs.jobs.values(), key=lambda j: -j.get("created", 0))[:100]
                return self._send(200, {"jobs": [j.public() for j in jobs]})
            job = app.jobs.get(rest[0]) if rest else None
            if job is None:
                return self._send(404, {"error": "no such job"})
            if method == "GET" and len(rest) == 1:
                return self._send(200, job.public())
            if method == "POST" and rest[1:] == ["cancel"]:
                ok = app.cancel(job.id)
                return self._send(200, {"ok": True, "cancelled": bool(ok), "status": job.get("status")})
            return self._send(404, {"error": "not found"})

        def _projects(self, method, name, rest, body):
            if not rest:
                if method == "PUT":
                    proj = app.projects.get(name, body.get("root") or None)
                    proj.put_meta(body)
                    return self._send(200, {"ok": True, "root": proj.root})
                if method == "GET":
                    proj = app.projects.get(name)
                    return self._send(200, {"project": proj.meta, "root": proj.root, "shots": sorted(proj.shots)})
            if rest == ["plan"] and method == "POST":
                shots = app.plan.plan(body.get("brief", ""), body.get("characters") or [])
                return self._send(200, {"shots": shots})
            if len(rest) >= 2 and rest[0] == "shots":
                return self._shot(method, app.projects.get(name), rest[1], rest[2:], body)
            return self._send(404, {"error": "not found"})

        def _shot(self, method, proj, shot_id, rest, body):
            if not rest:
                if method == "PUT":
                    if body.get("id") not in (None, shot_id):
                        raise ValueError(f"shot id {body.get('id')!r} does not match URL {shot_id!r}")
                    proj.put_shot(shot_id, body)
                    return self._send(200, {"ok": True})
                if method == "GET":
                    shot = proj.shots.get(shot_id)
                    if shot is None:
                        return self._send(404, {"error": "no such shot"})
                    with proj.lock:
                        stages = {k: {kk: vv for kk, vv in v.items()} for k, v in
                                  (proj.stages.get(shot_id) or {}).items()}
                        arts = {k: v.get("artifact") for k, v in (proj.artifacts.get(shot_id) or {}).items()}
                    return self._send(200, {"shot": shot, "stages": stages, "artifacts": arts})
            if method == "POST" and len(rest) == 1 and rest[0] in pipeline_kinds():
                if shot_id not in proj.shots:
                    return self._send(404, {"error": "PUT the shot first"})
                if rest[0] == "passes" and not body.get("snapshot_path"):
                    raise ValueError("passes body needs snapshot_path")
                job = app.submit(proj.name, shot_id, rest[0], body)
                return self._send(202, {"job_id": job.id, "status": job.get("status")})
            return self._send(404, {"error": "not found"})

    return Handler


def pipeline_kinds():
    return tuple(RUNNERS)


def serve(app, host, port, verbose=False):
    srv = ThreadingHTTPServer((host, port), make_handler(app, verbose))
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="JSON or TOML config file (see server/config.example.json)")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--data", dest="data_dir", help="job store and default project roots")
    ap.add_argument("--blender", help="Blender executable for retarget and passes")
    ap.add_argument("--fake", action="store_true", help="every backend fake (no models, no GPU)")
    ap.add_argument("--backend", action="append", default=[], metavar="STAGE=IMPL",
                    help="pick one backend, e.g. --backend passes=blender (repeatable)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a config value, e.g. --set motion.num_samples=6 (JSON values)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--print-config", action="store_true", help="print the effective config and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    cfg = config.build(a.config, {"host": a.host, "port": a.port, "data_dir": a.data_dir, "blender": a.blender,
                                  "cache": False if a.no_cache else None},
                       a.set, a.fake, a.backend)
    if a.print_config:
        print(json.dumps(cfg, indent=2))
        return
    app = Orchestrator(cfg)
    app.start_health()
    srv = serve(app, cfg["host"], cfg["port"], a.verbose)
    impls = ", ".join(f"{k}={b.impl}" for k, b in app._backends().items())
    print(f"orchestrator on http://{cfg['host']}:{srv.server_address[1]} ({impls}); data {cfg['data_dir']}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for job in list(app.jobs.jobs.values()):
            if job.get("status") not in TERMINAL:
                job.cancel()
        time.sleep(0.2)


if __name__ == "__main__":
    main()
