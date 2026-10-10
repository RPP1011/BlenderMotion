"""Stage logic: motion (+ ranking + retarget), passes, generate (+ eval).

Each `run_*` runs inside a job thread. GPU stages hold their device's lock
only while the model runs; Blender retarget and eval run outside it unless
configured with a device.
"""

import json
import os

from . import h3, kimodo, takes
from .state import cache_key

PROGRESS = {"motion": (0.02, 0.65), "retarget": 0.7}


def _shot_fps(shot):
    return int(shot.get("fps") or 24)


def _rig_inputs(cb):
    rig = cb.get("rig") or {}
    return {k: rig.get(k) for k in ("matrix_world", "bonemap", "bones")}


def _library(shot, cid, root):
    """{"path", "object"} from the shot's `rig: "assets/hero.blend#HeroRig"`, if it resolves."""
    for c in shot.get("characters") or []:
        ref = c.get("rig") or ""
        if c.get("char_id") == cid and "#" in ref:
            path, obj = ref.rsplit("#", 1)
            path = path[2:] if path.startswith("//") else path
            path = path if os.path.isabs(path) else os.path.join(root, path)
            if os.path.isfile(path):
                return {"path": path, "object": obj}
    return None


# -- motion ------------------------------------------------------------------------

def motion_inputs(app, shot, body):
    m = shot.get("motion") or {}
    mc = app.cfg["motion"]
    chars = {cid: {"constraints": cb.get("constraints") or [], "start": cb.get("start_matrix_world"),
                   "scale": cb.get("scale"), "rig": _rig_inputs(cb)}
             for cid, cb in sorted((body.get("characters") or {}).items())}
    return {
        "prompt": shot.get("action_prompt", ""), "frames": body.get("frames"), "fps": _shot_fps(shot),
        "duration_s": shot.get("duration_s"), "seed": m.get("seed", 0), "variants": m.get("variants", 2),
        "char_ids": [c.get("char_id") for c in shot.get("characters") or []],
        "rigs": [c.get("rig") for c in shot.get("characters") or []],
        "characters": chars, "backend": app.motion.impl, "retarget": app.retarget.impl,
        "num_samples": mc.get("num_samples"), "ranking": mc.get("ranking"), "max_rounds": mc.get("max_rounds"),
        "keep_rejected": mc.get("keep_rejected"),
    }


def _measure(job, outs, kc, ranking):
    loaded, items = {}, []
    for k, o in enumerate(outs):
        item = {"id": k, "bvh": o.get("bvh"), "npz": o.get("npz")}
        if not o.get("bvh"):
            job.warn(f"take {k}: no .bvh; skipped")
            continue
        if o.get("npz"):
            try:
                if o["npz"] not in loaded:
                    loaded[o["npz"]] = takes.load_takes(o["npz"])
                pj, rp = loaded[o["npz"]][o.get("index", 0) if len(loaded[o["npz"]]) > 1 else 0]
                item["posed_joints"], item["root_positions"] = pj, rp
            except (ValueError, OSError, IndexError) as e:
                job.warn(f"take {k}: cannot read {o['npz']}: {e}; kept unranked")
        items.append(item)
    ranked = [i for i in items if "posed_joints" in i]
    good, bad = takes.rank(ranked, kc, ranking)
    unranked = [i for i in items if "posed_joints" not in i]
    for i in unranked:
        i["metrics"] = {"score": 99.0, "rejected": False, "reasons": ["unranked (no npz)"]}
    for i in good + bad:
        i.pop("posed_joints", None)
        i.pop("root_positions", None)
    return good + unranked, bad


def run_motion(app, job, proj, shot_id, body):
    shot = proj.shots[shot_id]
    fps = _shot_fps(shot)
    frames = int(body.get("frames") or round(float(shot.get("duration_s") or 4.0) * fps))
    total_s = round(frames / fps, 4)
    mcfg = app.cfg["motion"]
    m = shot.get("motion") or {}
    variants = max(1, int(m.get("variants") or 2))
    num_samples = max(int(mcfg.get("num_samples") or 4), variants)
    seed = int(m.get("seed") or 0)
    chars_body = body.get("characters") or {}
    char_ids = [c["char_id"] for c in shot.get("characters") or [] if c.get("char_id")] or list(chars_body)
    if not char_ids:
        raise RuntimeError("shot has no characters")
    if total_s > kimodo.KIMODO_MAX_S:
        job.warn(f"{total_s:.1f} s is past Kimodo's ~{kimodo.KIMODO_MAX_S:.0f} s; quality may drop")
    if body.get("continuity"):
        job.warn("continuity (fullbody constraint from the previous shot) is not implemented; ignored")
    if len(char_ids) > 1:
        job.warn("multi-character: each character is generated independently (no interaction)")

    inputs = motion_inputs(app, shot, body)
    key = cache_key("motion", inputs)
    job.update(input_hash=key)
    if app.use_cache(body):
        hit = proj.cache_get("motion", key)
        if hit:
            job.log(f"cache hit (motion {key[:12]}), from {hit['job_id']}")
            job.update(cache_hit=True, stage="retarget")
            job.add_artifact(**hit["artifact"])
            proj.set_hash(shot_id, "motion", key)
            return
    out_dir = proj.cas_dir("motion", key)
    ranking = mcfg.get("ranking") or {}
    max_rounds = int(mcfg.get("max_rounds") or 0)
    lo, hi = PROGRESS["motion"]
    span = (hi - lo) / len(char_ids)
    chosen, report = {}, {}
    for ci, cid in enumerate(char_ids):
        cb = chars_body.get(cid) or {}
        kc, warns = kimodo.convert_constraints(cb, fps, frames)
        for w in warns:
            job.warn(f"{cid}: {w}")
        good_all, bad_all = [], []
        for r in range(max_rounds + 1):
            s = seed + ci * 1000 + r * 7919
            req = {"char_id": cid, "prompt": shot.get("action_prompt", ""), "total_s": total_s,
                   "frames_30": kimodo.frames_at(frames, fps), "seed": s, "num_samples": num_samples,
                   "constraints": kc, "out_dir": os.path.join(out_dir, "takes"),
                   "progress_span": span / (max_rounds + 1) * 0.9}
            with app.gpus.hold(job, app.motion.device) as env:
                job.set_stage("motion", lo + span * ci + span * r / (max_rounds + 1))
                outs = app.motion.generate(job, req, env)
            good, bad = _measure(job, outs, kc, ranking)
            for t in good + bad:
                mt = t["metrics"]
                job.log(f"{cid} seed {s} take {t['id']}: score {mt['score']} float {mt.get('float_frac')} "
                        f"step {mt.get('max_step')} err {mt.get('root_err')}"
                        + (f" REJECTED: {'; '.join(mt['reasons'])}" if mt.get("rejected") else ""))
            good_all += good
            bad_all += bad
            if len(good_all) >= variants:
                break
            if r < max_rounds:
                job.log(f"{cid}: {len(good_all)}/{variants} usable takes; another round")
        good_all.sort(key=lambda t: t["metrics"]["score"])
        if not good_all:
            reasons = sorted({x for t in bad_all for x in t["metrics"]["reasons"]})
            if not mcfg.get("keep_rejected") or not bad_all:
                raise RuntimeError(f"{cid}: all {len(bad_all)} Kimodo takes rejected ({'; '.join(reasons)}). "
                                   "Try another seed or looser constraints.")
            job.warn(f"{cid}: every take was rejected; returning the least bad one")
            good_all = sorted(bad_all, key=lambda t: t["metrics"]["score"])[:1]
        chosen[cid] = good_all[:variants]
        report[cid] = [{"bvh": t["bvh"], "npz": t.get("npz"), **t["metrics"]} for t in good_all + bad_all]
    n = min(len(v) for v in chosen.values())
    if n < variants:
        job.warn(f"only {n} of {variants} requested variants passed the take checks")
    with open(os.path.join(out_dir, "takes.json"), "w") as f:
        json.dump(report, f, indent=1)

    job.set_stage("retarget", PROGRESS["retarget"])
    job.update(status="running")
    spec = {"fps": fps, "frames": frames, "out_blend": os.path.join(out_dir, "motion.blend"),
            "out_json": os.path.join(out_dir, "actions.json"), "characters": {}}
    for cid in char_ids:
        cb = chars_body.get(cid) or {}
        spec["characters"][cid] = {
            "start_matrix_world": cb.get("start_matrix_world"), "scale": cb.get("scale") or 1.0,
            "rig": cb.get("rig"), "library": _library(shot, cid, proj.root),
            "takes": [{"bvh": t["bvh"], "variant": v} for v, t in enumerate(chosen[cid][:n])],
        }
        if not cb.get("rig") and not spec["characters"][cid]["library"]:
            job.warn(f"{cid}: no rig rest data or library rig; actions keep SOMA bone names")
    art = app.retarget.retarget(job, spec)
    job.add_artifact(**art)
    proj.cache_put("motion", key, art, job.id)
    proj.set_hash(shot_id, "motion", key)


# -- passes ------------------------------------------------------------------------

def passes_inputs(app, body):
    pc = app.cfg["passes"]
    keep = {k: v for k, v in body.items() if k not in ("snapshot_path",)}
    return {**keep, "backend": app.passes.impl, "still_engine": pc.get("still_engine"),
            "playblast_engine": pc.get("playblast_engine"), "resolution_scale": pc.get("resolution_scale")}


def run_passes(app, job, proj, shot_id, body):
    key = cache_key("passes", passes_inputs(app, body))
    job.update(input_hash=key)
    extra = {"frames_gen": body.get("frames_gen"), "resolution": body.get("resolution"),
             "characters": body.get("characters")}
    if app.use_cache(body):
        hit = proj.cache_get("passes", key)
        if hit:
            job.log(f"cache hit (passes {key[:12]})")
            job.update(cache_hit=True)
            job.add_artifact(**hit["artifact"])
            proj.set_artifact(shot_id, "passes", hit["artifact"], key, extra)
            proj.set_hash(shot_id, "passes", key)
            return
    out_dir = proj.cas_dir("passes", key)
    with app.gpus.hold(job, app.passes.device) as env:
        job.set_stage("passes", 0.1)
        art = app.passes.render(job, body, out_dir, env)
    job.add_artifact(**art)
    proj.set_artifact(shot_id, "passes", art, key, extra)
    proj.cache_put("passes", key, art, job.id)
    proj.set_hash(shot_id, "passes", key)


# -- generate + eval ------------------------------------------------------------------

def _file_sig(path):
    try:
        st = os.stat(path)
        return [path, st.st_size, int(st.st_mtime)]
    except OSError:
        return [path, None, None]


def generate_request(app, proj, shot, body, passes):
    gcfg = app.cfg["generate"]
    render = shot.get("render") or {}
    mode = body.get("mode") or render.get("mode") or "ref2v"
    if mode not in ("flf", "ref2v"):
        raise ValueError(f"unknown mode {mode!r} (flf | ref2v)")
    art = passes["artifact"]
    frames = None
    try:
        with open(art["kp2d"]) as f:
            frames = len(json.load(f).get("frames") or [])
    except (OSError, ValueError, KeyError):
        frames = passes.get("frames_gen")
    if not frames:
        frames = round(float(shot.get("duration_s") or 4.0) * h3.H3_FPS)
    frames = h3.snap_frames(frames)
    if frames > int(gcfg.get("max_frames") or h3.H3_MAX_FRAMES):
        raise RuntimeError(f"{frames} frames is past H3's {gcfg.get('max_frames')} per generation; split the shot")
    res = render.get("resolution") or passes.get("resolution") or (proj.meta or {}).get("resolution") or [960, 544]
    w, h = h3.snap_resolution(res[0], res[1], gcfg.get("resolution_multiple", 16))
    refs = h3.reference_images(shot, proj.root) if mode == "ref2v" else []
    prompt = h3.build_prompt(shot, mode, [label for _, label in refs], proj.meta)
    return {"mode": mode, "prompt": prompt, "frames": frames, "width": w, "height": h,
            "seed": int(render.get("seed") if render.get("seed") is not None else gcfg.get("seed", 42)),
            "first": art.get("first"), "last": art.get("last"), "playblast": art.get("playblast"),
            "kp2d": art.get("kp2d"), "images": [p for p, _ in refs]}


def run_generate(app, job, proj, shot_id, body):
    shot = proj.shots[shot_id]
    passes = proj.artifact(shot_id, "passes")
    if not passes:
        raise RuntimeError("no passes for this shot; render passes first")
    if (body.get("model") or "h3") != "h3":
        raise ValueError(f"model {body.get('model')!r} not supported (h3 only)")
    req = generate_request(app, proj, shot, body, passes)
    gcfg = {k: v for k, v in app.cfg["generate"].items() if k not in ("url", "comfy_root", "poll_s", "timeout_s",
                                                                      "websocket", "fake_delay_s")}
    inputs = {"req": {k: v for k, v in req.items() if k not in ("images", "first", "last", "playblast", "kp2d")},
              "passes_key": passes.get("key"), "images": [_file_sig(p) for p in req["images"]],
              "backend": app.generate.impl, "eval": app.evaluate.impl, "cfg": gcfg}
    key = cache_key("generate", inputs)
    job.update(input_hash=key)
    job.log(f"prompt: {req['prompt']}")
    if app.use_cache(body):
        hit = proj.cache_get("generate", key)
        if hit and "pck" in hit["artifact"]:  # only complete runs (video + eval) are cached
            job.log(f"cache hit (generate {key[:12]})")
            job.update(cache_hit=True, stage="eval")
            job.add_artifact(**{k: v for k, v in hit["artifact"].items() if v is not None})
            proj.set_hash(shot_id, "generate", key)
            return
    out_dir = proj.cas_dir("generate", key)
    req["out_path"] = os.path.join(out_dir, f"{shot_id}_{req['mode']}.mp4")
    req["progress_span"] = 0.8
    with open(os.path.join(out_dir, "request.json"), "w") as f:
        json.dump(req, f, indent=1)
    with app.gpus.hold(job, app.generate.device) as env:
        job.set_stage("generate", 0.05)
        out = app.generate.generate(job, req, env)
    art = {"video": out["video"]}
    if app.cfg["generate"].get("emit_audio") and app.generate.impl != "fake":
        art["audio"] = out["video"]
    job.add_artifact(**art)
    proj.set_hash(shot_id, "generate", key)

    job.set_stage("eval", 0.9)
    chars = list((passes.get("characters") or {}).keys()) or \
        [c["char_id"] for c in shot.get("characters") or [] if c.get("char_id")]
    with app.gpus.hold(job, app.evaluate.device) as env:
        res = app.evaluate.evaluate(job, req["kp2d"], out["video"], chars, os.path.join(out_dir, "pck.json"), env)
    art["pck"] = res.get("pck@0.1") if res else None
    if art["pck"] is not None:
        job.add_artifact(pck=art["pck"])
        job.log(f"PCK@0.1 {art['pck']}")
    proj.cache_put("generate", key, art, job.id)
