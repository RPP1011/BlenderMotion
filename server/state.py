"""Projects, shots, per-stage status and the cache index, persisted as JSON.

Layout under a project root (architecture §3.3, JSON instead of SQLite):

  project.json                    PUT /projects/{p} body
  shots/<id>.json                 PUT /projects/{p}/shots/{s} body (+ server `hashes`)
  cas/<stage>/<key[:16]>/...      stage outputs, addressed by input hash
  orchestrator/state.json         {stages: {shot: {stage: {status, job_id, hash}}},
                                   artifacts: {shot: {stage: {artifact, key}}}}
  orchestrator/cache_index.json   {stage: {key: {artifact, job_id, time}}}

The registry of project name -> root lives in <data_dir>/projects.json.
"""

import glob
import hashlib
import json
import os
import threading
import time

from .jobs import read_json, write_json

STAGE_VERSION = {"motion": 1, "passes": 1, "generate": 1}
CHAINS = {"motion": ["motion", "retarget"], "passes": ["passes"], "generate": ["generate", "eval"]}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def cache_key(stage, inputs):
    return hashlib.sha256(f"{stage}:{STAGE_VERSION.get(stage, 1)}:{canonical(inputs)}".encode()).hexdigest()


def _paths_exist(art):
    for k in ("blend", "video", "playblast", "kp2d", "first"):
        v = art.get(k)
        if isinstance(v, str) and not os.path.exists(v):
            return False
    return True


class Project:
    def __init__(self, name, root):
        self.name = name
        self.root = os.path.abspath(os.path.expanduser(root))
        self.lock = threading.RLock()
        self.meta = read_json(os.path.join(self.root, "project.json"), {}) or {}
        self.shots = {}
        for path in glob.glob(os.path.join(self.root, "shots", "*.json")):
            d = read_json(path)
            if isinstance(d, dict):
                self.shots[os.path.splitext(os.path.basename(path))[0]] = d
        st = read_json(self._state_path(), {}) or {}
        self.stages = st.get("stages", {})
        self.artifacts = st.get("artifacts", {})
        self.cache = read_json(self._cache_path(), {}) or {}

    def _state_path(self):
        return os.path.join(self.root, "orchestrator", "state.json")

    def _cache_path(self):
        return os.path.join(self.root, "orchestrator", "cache_index.json")

    def save_state(self):
        with self.lock:
            write_json(self._state_path(), {"stages": self.stages, "artifacts": self.artifacts})

    def put_meta(self, body):
        with self.lock:
            self.meta = body
            write_json(os.path.join(self.root, "project.json"), body)

    def put_shot(self, shot_id, body):
        with self.lock:
            old = self.shots.get(shot_id) or {}
            if old.get("hashes"):
                body = dict(body)
                body["hashes"] = {**(body.get("hashes") or {}), **old["hashes"]}
            self.shots[shot_id] = body
            write_json(os.path.join(self.root, "shots", shot_id + ".json"), body)

    def set_hash(self, shot_id, stage, key):
        with self.lock:
            shot = self.shots.get(shot_id)
            if shot is None:
                return
            shot.setdefault("hashes", {})[stage] = key
            write_json(os.path.join(self.root, "shots", shot_id + ".json"), shot)

    def cas_dir(self, stage, key):
        d = os.path.join(self.root, "cas", stage, key[:16])
        os.makedirs(d, exist_ok=True)
        return d

    def cache_get(self, stage, key):
        with self.lock:
            hit = (self.cache.get(stage) or {}).get(key)
        if hit and _paths_exist(hit.get("artifact") or {}):
            return hit
        return None

    def cache_put(self, stage, key, artifact, job_id):
        with self.lock:
            self.cache.setdefault(stage, {})[key] = {"artifact": artifact, "job_id": job_id, "time": time.time()}
            write_json(self._cache_path(), self.cache)

    def set_artifact(self, shot_id, stage, artifact, key=None, extra=None):
        with self.lock:
            self.artifacts.setdefault(shot_id, {})[stage] = {"artifact": artifact, "key": key, **(extra or {})}
            self.save_state()

    def artifact(self, shot_id, stage):
        with self.lock:
            return (self.artifacts.get(shot_id) or {}).get(stage)

    def record_job(self, job_data):
        """Mirror a job's stage/status into the per-shot stage table."""
        kind, stage, status = job_data["kind"], job_data["stage"], job_data["status"]
        chain = CHAINS.get(kind, [kind])
        idx = chain.index(stage) if stage in chain else 0
        with self.lock:
            rec = self.stages.setdefault(job_data["shot"], {})
            for s in chain[:idx]:
                rec[s] = {"status": "done", "job_id": job_data["id"]}
            rec[stage] = {"status": status, "job_id": job_data["id"], "hash": job_data.get("input_hash", "")}
            if status in ("failed", "cancelled"):
                for s in chain[idx + 1:]:
                    if (rec.get(s) or {}).get("job_id") == job_data["id"] or s not in rec:
                        rec[s] = {"status": status, "job_id": job_data["id"]}
            self.save_state()


class Projects:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.reg_path = os.path.join(data_dir, "projects.json")
        self.roots = read_json(self.reg_path, {}) or {}
        self.items = {}
        self.lock = threading.Lock()

    def get(self, name, root=None):
        with self.lock:
            if root:
                root = os.path.abspath(os.path.expanduser(root))
            p = self.items.get(name)
            if p is not None and (not root or root == p.root):
                return p
            root = root or self.roots.get(name) or os.path.join(self.data_dir, "projects", name)
            p = Project(name, root)
            self.items[name] = p
            if self.roots.get(name) != p.root:
                self.roots[name] = p.root
                write_json(self.reg_path, self.roots)
            return p

    def known(self, name):
        return name in self.items or name in self.roots
