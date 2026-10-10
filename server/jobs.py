"""Jobs: persistent store, GPU locks, cancellable subprocesses.

States: queued -> waiting_gpu -> running -> done | failed | cancelled
(the add-on's STATUS_ITEMS). `stage` may advance inside one job
(motion -> retarget, generate -> eval).
"""

import contextlib
import json
import os
import signal
import subprocess
import threading
import time
import uuid

TERMINAL = ("done", "failed", "cancelled")
PUBLIC = ("id", "project", "shot", "kind", "stage", "status", "progress", "artifact", "error",
          "warnings", "created", "updated", "cache_hit", "input_hash")


class Cancelled(Exception):
    pass


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Job:
    def __init__(self, store, project, shot, kind, stage=None, job_id=None):
        self.store = store
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self._cancel_hooks = []
        self.logs = []
        self.data = {
            "id": job_id or "job_" + uuid.uuid4().hex[:12], "project": project, "shot": shot, "kind": kind,
            "stage": stage or kind, "status": "queued", "progress": 0.0, "artifact": {}, "error": "",
            "warnings": [], "created": time.time(), "updated": time.time(), "cache_hit": False,
            "input_hash": "",
        }

    @property
    def id(self):
        return self.data["id"]

    def get(self, key, default=None):
        return self.data.get(key, default)

    def public(self):
        with self.lock:
            out = {k: self.data.get(k) for k in PUBLIC}
            out["artifact"] = dict(out["artifact"] or {})
            out["warnings"] = list(out["warnings"] or [])
            out["logs_tail"] = self.logs[-15:]
            return out

    def update(self, **kw):
        with self.lock:
            self.data.update(kw)
            self.data["updated"] = time.time()
        self.store.changed(self)

    # -- used by backends --------------------------------------------------------------
    def set_stage(self, stage, progress=None):
        self.check_cancel()
        kw = {"stage": stage}
        if progress is not None:
            kw["progress"] = float(progress)
        self.update(**kw)

    def set_progress(self, p):
        p = max(0.0, min(1.0, float(p)))
        if abs(p - self.data["progress"]) >= 0.005 or p in (0.0, 1.0):
            self.update(progress=round(p, 4))

    def add_artifact(self, **kw):
        with self.lock:
            art = dict(self.data["artifact"] or {})
            art.update(kw)
        self.update(artifact=art)

    def warn(self, msg):
        self.log("WARNING: " + msg)
        with self.lock:
            w = list(self.data["warnings"]) + [msg]
        self.update(warnings=w)

    def log(self, line):
        line = line.rstrip("\n")
        with self.lock:
            self.logs.append(line)
            if len(self.logs) > 400:
                del self.logs[:200]
        self.store.append_log(self, line)

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def on_cancel(self, fn):
        """Register a callback run (once) when the job is cancelled."""
        self._cancel_hooks.append(fn)

    def cancel(self):
        if self.data["status"] in TERMINAL:
            return False
        self.cancel_event.set()
        for fn in list(self._cancel_hooks):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.log(f"cancel hook failed: {e!r}")
        return True

    def sleep(self, seconds):
        if self.cancel_event.wait(seconds):
            raise Cancelled()

    def run(self, cmd, env=None, cwd=None, timeout=None, ok_marker=None, on_line=None):
        """Run a subprocess, streaming its output into the job log.

        Killed (process group) on cancel or timeout. Raises RuntimeError on a
        non-zero exit or a missing ok_marker. Returns the output tail."""
        self.check_cancel()
        self.log("$ " + " ".join(str(c) for c in cmd))
        full_env = dict(os.environ)
        full_env.update({k: str(v) for k, v in (env or {}).items()})
        try:
            proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=full_env, cwd=cwd or None, text=True,
                                    errors="replace", bufsize=1, start_new_session=True)
        except OSError as e:
            raise RuntimeError(f"cannot start {cmd[0]}: {e}") from None
        tail, seen_ok = [], [False]

        def kill():
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        os.killpg(proc.pid, signal.SIGKILL)

        self.on_cancel(kill)

        def reader():
            for line in proc.stdout:
                tail.append(line.rstrip("\n"))
                del tail[:-60]
                self.log(line)
                if ok_marker and ok_marker in line:
                    seen_ok[0] = True
                if on_line:
                    try:
                        on_line(line)
                    except Exception:  # noqa: BLE001
                        pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t0 = time.monotonic()
        try:
            while proc.poll() is None:
                if self.cancel_event.is_set():
                    kill()
                    raise Cancelled()
                if timeout and time.monotonic() - t0 > timeout:
                    kill()
                    raise RuntimeError(f"{os.path.basename(str(cmd[0]))} timed out after {timeout:.0f} s")
                time.sleep(0.1)
            t.join(5)
        finally:
            self._cancel_hooks = [h for h in self._cancel_hooks if h is not kill]
        if self.cancel_event.is_set():
            raise Cancelled()
        if proc.returncode != 0 or (ok_marker and not seen_ok[0]):
            why = f"exit {proc.returncode}" if proc.returncode else f"no {ok_marker} in output"
            raise RuntimeError(f"{os.path.basename(str(cmd[0]))} failed ({why}): " + " | ".join(tail[-6:]))
        return tail


class GpuPool:
    """One lock per named GPU. Stages on the same card run one at a time."""

    def __init__(self, gpus):
        self.gpus = gpus or {}
        self._locks = {name: threading.Lock() for name in self.gpus}
        self.holders = {}
        self._mx = threading.Lock()

    def env(self, device):
        return dict((self.gpus.get(device) or {}).get("env") or {}) if device else {}

    @contextlib.contextmanager
    def hold(self, job, device):
        """Wait (status waiting_gpu) for the device, then mark the job running."""
        if not device:
            job.update(status="running")
            yield {}
            return
        with self._mx:
            lock = self._locks.setdefault(device, threading.Lock())
        if not lock.acquire(blocking=False):
            job.update(status="waiting_gpu")
            job.log(f"waiting for {device} (held by {self.holders.get(device)})")
            while not lock.acquire(timeout=0.25):
                job.check_cancel()
        try:
            self.holders[device] = f"{job.id} ({job.get('stage')})"
            job.update(status="running")
            yield self.env(device)
        finally:
            self.holders.pop(device, None)
            lock.release()

    def status(self):
        return {name: ("busy: " + self.holders[name]) if name in self.holders else "idle"
                for name in sorted(set(self._locks) | set(self.gpus))}


class JobStore:
    """Jobs as JSON files under <data_dir>/jobs; logs next to them."""

    def __init__(self, data_dir, on_change=None):
        self.dir = os.path.join(data_dir, "jobs")
        os.makedirs(self.dir, exist_ok=True)
        self.jobs = {}
        self.on_change = on_change
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        for name in os.listdir(self.dir):
            if not name.endswith(".json"):
                continue
            data = read_json(os.path.join(self.dir, name))
            if not data or "id" not in data:
                continue
            job = Job(self, data["project"], data["shot"], data.get("kind", data.get("stage")), job_id=data["id"])
            job.data.update(data)
            if job.data["status"] not in TERMINAL:  # interrupted by a restart
                job.data["status"] = "failed"
                job.data["error"] = "orchestrator restarted while the job was running"
                write_json(self.path(job), job.data)
            self.jobs[job.id] = job

    def path(self, job):
        return os.path.join(self.dir, job.id + ".json")

    def new(self, project, shot, kind, stage=None):
        job = Job(self, project, shot, kind, stage)
        with self._lock:
            self.jobs[job.id] = job
        self.changed(job)
        return job

    def get(self, job_id):
        return self.jobs.get(job_id)

    def changed(self, job):
        with job.lock:
            snapshot = dict(job.data)
        write_json(self.path(job), snapshot)
        if self.on_change:
            self.on_change(job)

    def append_log(self, job, line):
        try:
            with open(os.path.join(self.dir, job.id + ".log"), "a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def counts(self):
        out = {}
        for j in list(self.jobs.values()):
            s = j.data["status"]
            if s not in TERMINAL:
                out[s] = out.get(s, 0) + 1
        return out
