"""Main-thread side of the job pipeline (docs §5.2 step 3) and staleness tracking.

A persistent `bpy.app.timers` tick drains `events.QUEUE` and applies results.
Artifacts are applied by key and idempotently, so a job may report partial
artifacts while still running (e.g. the video before eval finishes):

  {"blend": path, "actions": [{"char", "action", "variant"}]}  -> import motion variants
  {"playblast": path}                                          -> muted VSE strip, channel 1
  {"video": path}                                              -> VSE strip VID_<id>, channel 2
  {"audio": path}                                              -> VSE sound strip, channel 3
  {"pck": float}                                               -> score badge
"""

import time

import bpy
from bpy.app.handlers import persistent

from . import events, prefs, props, scene_io, snapshot

TICK_S = 0.25
HASH_DEBOUNCE_S = 1.0
DOWNSTREAM = {"passes": ("generate", "eval"), "generate": ("eval",)}

_dirty_since = None
_applied = set()  # (job_id, artifact key, value) already applied


def _scene_by_name(name):
    return bpy.data.scenes.get(name) if name else None


def apply_event(ev):
    if ev["type"] == "health":
        if ev["ok"]:
            data = ev.get("data") or {}
            status = "ok"
            detail = " · ".join(f"{k}: {v}" for k, v in sorted(data.items())
                                if isinstance(v, (str, int, float, bool)))
        else:
            status, detail = "down", ev.get("error", "")[:200]
        for scene in bpy.data.scenes:
            p = scene.chimera
            # Write only on change: every write is a depsgraph update.
            if p.server_status != status or p.server_detail != detail:
                p.server_status, p.server_detail = status, detail
                _tag_redraw()
        return

    scene = _scene_by_name(ev.get("scene"))
    if scene is None:
        return
    shot = scene.chimera.find_shot(ev["shot"])
    if shot is None:
        return
    stage_key = ev["stage"] if ev["stage"] in props.STAGES else "motion"
    st = shot.stage(stage_key)
    st.job_id = ev["job_id"]
    st.status = ev["status"] if ev["status"] in {i[0] for i in props.STATUS_ITEMS} else "running"
    st.progress = ev["progress"]
    st.message = ev.get("error", "")[:200]
    if ev["status"] == "failed":
        print(f"[chimera] {shot.name}/{stage_key} failed: {ev.get('error')}")
    try:
        _apply_artifact(scene, shot, ev["job_id"], ev.get("artifact") or {})
    except Exception as e:  # never let one bad artifact kill the timer
        st.status = "failed"
        st.message = f"applying result: {e}"[:200]
        print(f"[chimera] applying artifact for {shot.name}: {e!r}")
    _tag_redraw()


def _once(job_id, key, value):
    k = (job_id, key, repr(value))
    if k in _applied:
        return False
    _applied.add(k)
    return True


def _apply_artifact(scene, shot, job_id, art):
    if "actions" in art and "blend" in art and _once(job_id, "actions", art["actions"]):
        created = scene_io.import_actions(bpy.path.abspath(art["blend"]), art["actions"], shot.name)
        firsts = [min(v) for v in created.values() if v]
        if firsts:
            scene_io.assign_variant(scene, shot, min(firsts))
            shot.motion_source = "generated"
        for key in ("retarget", "motion"):
            shot.stage(key).status = "done"
        mark_dirty()
    edit = None
    for key, kind, channel, mute in (("playblast", "movie", 1, True), ("video", "movie", 2, False),
                                     ("audio", "sound", 3, False)):
        if key in art and _once(job_id, key, art[key]):
            rng = scene_io.shot_range(scene, shot.name)
            edit = edit or scene_io.edit_scene(scene)
            prefix = {"playblast": "PB", "video": "VID", "audio": "AUD"}[key]
            scene_io.place_strip(edit, f"{prefix}_{shot.name}", kind, bpy.path.abspath(art[key]),
                                 channel, rng[0] if rng else scene.frame_start, mute=mute)
            if key == "video":
                shot.stage("generate").status = "done"
    if "pck" in art and art["pck"] is not None:
        shot.pck = float(art["pck"])
        shot.stage("eval").status = "done"


def mark_dirty():
    global _dirty_since
    if _dirty_since is None:
        _dirty_since = time.monotonic()


def refresh_hashes(scene):
    """Recompute per-shot hashes; mark downstream stages stale and detect manual edits."""
    project = scene.chimera
    scene_io.durations_from_markers(scene, project)
    for shot in project.shots:
        h = snapshot.scene_hash(scene, shot)
        if h != shot.scene_hash:
            shot.scene_hash = h
        for key in ("passes",):
            st = shot.stages.get(key)
            if st and st.status == "done" and st.input_hash and st.input_hash != h:
                st.status = "stale"
                for down in DOWNSTREAM[key]:
                    d = shot.stages.get(down)
                    if d and d.status == "done":
                        d.status = "stale"
        ch = snapshot.constraints_hash(shot)
        if ch != shot.constraints_hash:
            if shot.constraints_hash:
                st = shot.stages.get("motion")
                if st and st.status == "done":
                    st.status = "stale"
            shot.constraints_hash = ch
        for _char, strip in scene_io.shot_strips(shot):
            act = strip.action
            if act and act.get("chimera_import_hash") and shot.motion_source != "manual":
                if scene_io.action_hash(act) != act["chimera_import_hash"]:
                    shot.motion_source = "manual"


def tick():
    global _dirty_since
    p = prefs.get()
    if p is not None:
        events.WATCHER.base_url = p.server_url.rstrip("/")
        events.WATCHER.interval = p.poll_interval
    try:
        while True:
            apply_event(events.QUEUE.get_nowait())
    except Exception as e:
        if not isinstance(e, events.queue.Empty):
            print(f"[chimera] event error: {e!r}")
    if _dirty_since is not None and time.monotonic() - _dirty_since > HASH_DEBOUNCE_S:
        _dirty_since = None
        for scene in bpy.data.scenes:
            if len(scene.chimera.shots):
                try:
                    refresh_hashes(scene)
                except Exception as e:
                    print(f"[chimera] hash refresh failed: {e!r}")
                _tag_redraw()
    return TICK_S


def _tag_redraw():
    wm = getattr(bpy.context, "window_manager", None)
    if not wm:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type in {"VIEW_3D", "SEQUENCE_EDITOR", "NLA_EDITOR"}:
                area.tag_redraw()


def resume_watching():
    """After a file load, keep following jobs that were still in flight."""
    for scene in bpy.data.scenes:
        for shot in scene.chimera.shots:
            for st in shot.stages:
                if st.job_id and st.status in props.ACTIVE_STATUSES:
                    events.WATCHER.watch(st.job_id, scene.name, shot.name, st.name)


@persistent
def _on_depsgraph(scene, depsgraph):
    for upd in depsgraph.updates:
        if isinstance(upd.id, (bpy.types.Object, bpy.types.Action, bpy.types.Camera, bpy.types.Scene)):
            mark_dirty()
            return


@persistent
def _on_load(_dummy):
    _applied.clear()
    resume_watching()
    mark_dirty()


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=TICK_S, persistent=True)
    events.WATCHER.start()
    bpy.app.timers.register(lambda: (resume_watching(), None)[1], first_interval=0.1)


def unregister():
    events.WATCHER.stop()
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load)):
        if fn in lst:
            lst.remove(fn)
