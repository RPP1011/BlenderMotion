"""Orchestrator HTTP client (docs §6). Stdlib only; safe to call from any thread."""

import json
import urllib.error
import urllib.parse
import urllib.request


class OrchestratorError(RuntimeError):
    pass


class Client:
    def __init__(self, base_url, timeout=5.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise OrchestratorError(f"{method} {path}: HTTP {e.code} {detail}") from None
        except (urllib.error.URLError, OSError) as e:
            raise OrchestratorError(f"{method} {path}: {getattr(e, 'reason', e)}") from None
        return json.loads(raw) if raw else {}

    # -- projects / shots ----------------------------------------------------
    def _p(self, project):
        return "/projects/" + urllib.parse.quote(project, safe="")

    def _s(self, project, shot):
        return self._p(project) + "/shots/" + urllib.parse.quote(shot, safe="")

    def put_project(self, project, body):
        return self._request("PUT", self._p(project), body)

    def plan(self, project, brief, characters):
        # Planning runs an LLM synchronously; allow it time.
        return self._request("POST", self._p(project) + "/plan",
                             {"brief": brief, "characters": characters}, timeout=300)

    def put_shot(self, project, shot_id, shot_json):
        return self._request("PUT", self._s(project, shot_id), shot_json)

    def get_shot(self, project, shot_id):
        return self._request("GET", self._s(project, shot_id))

    def motion(self, project, shot_id, body):
        return self._request("POST", self._s(project, shot_id) + "/motion", body)

    def passes(self, project, shot_id, body):
        return self._request("POST", self._s(project, shot_id) + "/passes", body)

    def generate(self, project, shot_id, mode):
        return self._request("POST", self._s(project, shot_id) + "/generate", {"model": "h3", "mode": mode})

    # -- jobs ----------------------------------------------------------------
    def job(self, job_id):
        return self._request("GET", "/jobs/" + urllib.parse.quote(job_id, safe=""))

    def cancel(self, job_id):
        return self._request("POST", "/jobs/" + urllib.parse.quote(job_id, safe="") + "/cancel", {})

    def health(self, timeout=2.0):
        return self._request("GET", "/health", timeout=timeout)
