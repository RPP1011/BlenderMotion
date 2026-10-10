"""Planner: brief -> shot drafts. `heuristic` splits sentences (no model);
`llm` asks a local OpenAI-compatible server (llama.cpp, vLLM, ...) and falls
back to the heuristic when its answer is not usable. No hosted APIs."""

import json
import re
import urllib.error
import urllib.request

from .base import Backend

SYSTEM = ("You split a film brief into shots for a text-to-motion model. Reply with JSON only: "
          '{"shots": [{"action_prompt": "...", "duration_s": 4.0, "look": "..."}]}. One physical action '
          "per shot, described as body movement of one person, 2-10 seconds each.")


def draft(i, action, chars, duration, look=""):
    return {
        "id": f"sh_{(i + 1) * 10:03d}", "order": (i + 1) * 10, "duration_s": float(duration), "fps": 24,
        "characters": [{"char_id": c["char_id"], "rig": c.get("rig", ""), "refs": c.get("refs") or [],
                        "lora": None} for c in chars[:1]],
        "action_prompt": action,
        "motion": {"backend": "kimodo", "seed": 1000 + i, "variants": 2},
        "camera": {"mode": "authored"}, "look": {"prompt": look},
        "render": {"model": "h3", "mode": "ref2v"},
    }


class HeuristicPlan(Backend):
    kind, impl = "plan", "heuristic"

    def health(self):
        return "heuristic (sentence split)"

    def plan(self, brief, chars):
        chars = chars or [{"char_id": "hero", "rig": ""}]
        beats = [s.strip() for s in re.split(r"[.\n]+", brief or "") if len(s.strip()) > 8]
        n = int(self.cfg.get("max_shots", 8))
        return [draft(i, b, chars, self.cfg.get("default_duration_s", 4.0)) for i, b in enumerate(beats[:n])]


class LLMPlan(HeuristicPlan):
    kind, impl = "plan", "llm"

    def health(self):
        try:
            base = self.cfg["url"].split("/v1/")[0]
            with urllib.request.urlopen(base + "/v1/models", timeout=2) as r:
                r.read()
            return f"ok ({base})"
        except (urllib.error.URLError, OSError) as e:
            return f"down ({getattr(e, 'reason', e)}); heuristic fallback"

    def plan(self, brief, chars):
        body = {"model": self.cfg.get("model", "local"), "temperature": 0.3,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": brief}]}
        try:
            req = urllib.request.Request(self.cfg["url"], json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.cfg.get("timeout_s", 240)) as r:
                text = json.load(r)["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", text, re.S)
            shots = json.loads(m.group(0))["shots"] if m else []
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
            shots = []
        if not shots:
            return super().plan(brief, chars)
        chars = chars or [{"char_id": "hero", "rig": ""}]
        out = []
        for i, s in enumerate(shots[: int(self.cfg.get("max_shots", 8))]):
            dur = min(max(float(s.get("duration_s") or 4.0), 2.0), 10.0)
            out.append(draft(i, str(s.get("action_prompt", "")).strip(), chars, dur, str(s.get("look") or "")))
        return out


IMPLS = {"heuristic": HeuristicPlan, "fake": HeuristicPlan, "llm": LLMPlan}
