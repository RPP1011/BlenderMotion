"""Shot JSON <-> PropertyGroup conversion (docs §3.1)."""

import json


def _merge(base, over):
    """Recursive dict merge; `over` wins."""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def shot_to_dict(shot, project):
    modelled = {
        "id": shot.name,
        "project": project.project_name,
        "order": shot.order,
        "duration_s": round(shot.duration_s, 4),
        "fps": project.fps,
        "characters": [
            {
                "char_id": c.char_id,
                "rig": c.rig,
                "refs": [r.path for r in c.refs],
                "lora": c.lora or None,
            }
            for c in shot.characters
        ],
        "action_prompt": shot.action_prompt,
        "motion": {
            "backend": shot.motion_backend,
            "seed": shot.motion_seed,
            "variants": shot.motion_variants,
            "selected_variant": shot.selected_variant if shot.selected_variant >= 0 else None,
            "source": shot.motion_source,
            "continuity": shot.continuity,
        },
        "camera": {
            "mode": shot.camera_mode,
            "template": shot.camera_template,
            "object": f"CAM_{shot.name}",
        },
        "look": {"prompt": shot.look_prompt or project.look_prompt},
        "render": {
            "model": "h3",
            "mode": shot.h3_mode,
            "audio_prompt": shot.audio_prompt,
            "resolution": [shot.resolution_x, shot.resolution_y],
        },
        "eval": {"identity_manual": shot.identity_score or None},
    }
    extra = json.loads(shot.extra_json) if shot.extra_json else {}
    # VACE was dropped: never write the old path-A/B render layout back out.
    for legacy in ("path", "a", "b"):
        extra.get("render", {}).pop(legacy, None)
    return _merge(extra, modelled)


def dict_to_shot(d, shot, project=None):
    """Load a contract dict into `shot`. Missing keys keep their current value."""
    g = lambda *path, default=None: _get(d, path, default)  # noqa: E731
    shot.name = d.get("id", shot.name)
    shot.order = int(g("order", default=shot.order))
    shot.duration_s = float(g("duration_s", default=shot.duration_s))
    shot.action_prompt = g("action_prompt", default=shot.action_prompt) or ""

    chars = d.get("characters")
    if chars is not None:
        existing = {c.char_id: c.rig_object for c in shot.characters}
        shot.characters.clear()
        for cd in chars:
            c = shot.characters.add()
            c.char_id = cd.get("char_id", "char")
            c.rig = cd.get("rig") or ""
            c.lora = cd.get("lora") or ""
            for path in cd.get("refs") or []:
                c.refs.add().path = path
            if existing.get(c.char_id) is not None:
                c.rig_object = existing[c.char_id]

    shot.motion_backend = g("motion", "backend", default=shot.motion_backend)
    shot.motion_seed = int(g("motion", "seed", default=shot.motion_seed))
    shot.motion_variants = int(g("motion", "variants", default=shot.motion_variants))
    sel = g("motion", "selected_variant", default=None)
    shot.selected_variant = -1 if sel is None else int(sel)
    shot.motion_source = g("motion", "source", default=shot.motion_source)
    shot.continuity = bool(g("motion", "continuity", default=shot.continuity))

    shot.camera_mode = g("camera", "mode", default=shot.camera_mode)
    shot.camera_template = g("camera", "template", default=shot.camera_template) or ""

    look = g("look", "prompt", default="") or ""
    shot.look_prompt = "" if project and look == project.look_prompt else look

    mode = g("render", "mode", default=None) or g("render", "b", "mode", default=None)  # legacy "b" block
    if mode in ("flf", "ref2v"):
        shot.h3_mode = mode
    audio = g("render", "audio_prompt", default=None)
    if audio is None:
        audio = g("render", "b", "audio_prompt", default=None)
    if audio is not None:
        shot.audio_prompt = audio
    res = g("render", "resolution", default=None)
    if res:
        shot.resolution_x, shot.resolution_y = int(res[0]), int(res[1])
    ident = g("eval", "identity_manual", default=None)
    shot.identity_score = int(ident or 0)

    shot.extra_json = json.dumps(d, sort_keys=True)


def _get(d, path, default):
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return default
        d = d[key]
    return default if d is None and default is not None else d


def project_to_dict(project):
    return {
        "project": project.project_name,
        "fps": project.fps,
        "resolution": [project.resolution_x, project.resolution_y],
        "look": {"prompt": project.look_prompt},
        "shot_order": [s.name for s in sorted(project.shots, key=lambda s: s.order)],
    }
