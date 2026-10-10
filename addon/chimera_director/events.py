"""Background job watcher (docs §5.2 step 2).

Deviation from the spec: this polls `GET /jobs/{id}` instead of holding a
WebSocket on `/events`. A WS client needs either a third-party package in
Blender's Python or a hand-rolled RFC 6455 implementation; polling a handful
of jobs at 2 Hz costs nothing on localhost. Events have the same shape as the
spec's WS payload, so swapping the transport later only touches this module.

Nothing here touches bpy. Results go onto `QUEUE`; sync.py applies them on
the main thread.
"""

import queue
import threading
import time

from .client import Client, OrchestratorError

QUEUE = queue.Queue()
TERMINAL = {"done", "failed", "cancelled"}
HEALTH_EVERY_S = 5.0


class Watcher:
    def __init__(self):
        self._jobs = {}  # job_id -> {"scene", "shot", "stage", "last"}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.base_url = "http://127.0.0.1:8765"
        self.interval = 0.5

    def watch(self, job_id, scene, shot, stage):
        with self._lock:
            self._jobs[job_id] = {"scene": scene, "shot": shot, "stage": stage, "last": None}

    def unwatch(self, job_id):
        with self._lock:
            self._jobs.pop(job_id, None)

    def watching(self):
        with self._lock:
            return set(self._jobs)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="chimera-watcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self):
        last_health, last_url = 0.0, None
        while not self._stop.is_set():
            client = Client(self.base_url, timeout=3.0)
            if self.base_url != last_url or time.monotonic() - last_health > HEALTH_EVERY_S:
                last_url = self.base_url
                last_health = time.monotonic()
                try:
                    QUEUE.put({"type": "health", "ok": True, "data": client.health()})
                except OrchestratorError as e:
                    QUEUE.put({"type": "health", "ok": False, "error": str(e)})
            with self._lock:
                jobs = list(self._jobs.items())
            for job_id, meta in jobs:
                try:
                    data = client.job(job_id)
                except OrchestratorError as e:
                    if "HTTP 404" in str(e):
                        data = {"status": "failed", "error": "job unknown to orchestrator"}
                    else:
                        continue  # transient; retry next tick
                event = {
                    "type": "job",
                    "job_id": job_id,
                    "scene": meta["scene"],
                    "shot": data.get("shot", meta["shot"]),
                    "stage": data.get("stage", meta["stage"]),
                    "status": data.get("status", "running"),
                    "progress": float(data.get("progress") or 0.0),
                    "artifact": data.get("artifact") or {},
                    "error": data.get("error") or "",
                }
                key = (event["stage"], event["status"], round(event["progress"], 3),
                       repr(sorted(event["artifact"].items())))
                if key != meta["last"]:
                    meta["last"] = key
                    QUEUE.put(event)
                if event["status"] in TERMINAL:
                    self.unwatch(job_id)
            self._stop.wait(self.interval)


WATCHER = Watcher()
