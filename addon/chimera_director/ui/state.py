"""Derived UI state shared by panels and overlays: stage summary, colours, next step."""

from .. import props, scene_io

H3_MAX_S = 15.0       # one H3 generation
MOTION_MAX_S = 10.0   # one Kimodo call

# RGBA for overlays, by stage status.
COLORS = {
    "none": (0.35, 0.35, 0.38, 1.0),
    "queued": (0.30, 0.50, 0.85, 1.0),
    "waiting_gpu": (0.30, 0.50, 0.85, 1.0),
    "running": (0.30, 0.60, 1.00, 1.0),
    "done": (0.30, 0.72, 0.42, 1.0),
    "failed": (0.90, 0.30, 0.28, 1.0),
    "cancelled": (0.55, 0.45, 0.40, 1.0),
    "stale": (0.95, 0.65, 0.20, 1.0),
}

# Stages shown to the user. Retarget is folded into Motion.
PIPELINE = (("motion", "Motion", "ARMATURE_DATA"), ("passes", "Passes", "RENDERLAYERS"),
            ("generate", "Video", "FILE_MOVIE"), ("eval", "Eval", "CHECKMARK"))


def status(shot, key):
    st = shot.stages.get(key)
    return st.status if st else "none"


def overall(shot):
    """One status for the whole shot, for list rows and timeline bands."""
    states = [status(shot, k) for k, _l, _i in PIPELINE]
    for s in ("failed", "running", "waiting_gpu", "queued", "stale"):
        if s in states:
            return s
    if status(shot, "generate") == "done":
        return "done"
    return "none"


def active_job(shot):
    for key, label, _icon in PIPELINE:
        st = shot.stages.get(key)
        if st and st.status in props.ACTIVE_STATUSES:
            return key, label, st
    return None


def has_motion(shot):
    return any(True for _ in scene_io.shot_strips(shot))


def next_step(shot):
    """(operator idname or None, label, icon, hint). The one thing to do next."""
    job = active_job(shot)
    if job:
        _key, label, st = job
        return None, f"{label}: {st.status.replace('_', ' ')} {int(st.progress * 100)}%", "SORTTIME", \
            "Runs in the background; keep working"
    if not shot.characters:
        return "chimera.character_add", "Add a Character", "COMMUNITY", "Every shot needs a character"
    if any(c.rig_object is None for c in shot.characters):
        return None, "Assign a Rig", "ARMATURE_DATA", "Set the rig in Characters below, or link it from its .blend"
    if not shot.action_prompt.strip():
        return "chimera.edit_prompt", "Describe the Action", "GREASEPENCIL", "What does the character do?"
    m = status(shot, "motion")
    if not has_motion(shot) or m in ("failed", "stale"):
        hint = "Targets changed since the last generation" if m == "stale" else "Kimodo makes the variants"
        return "chimera.generate_motion", "Generate Motion", "ARMATURE_DATA", hint
    p = status(shot, "passes")
    if p != "done":
        hint = "The shot changed since the last passes" if p == "stale" else "Snapshot + headless render"
        return "chimera.render_passes", "Render Passes", "RENDERLAYERS", hint
    g = status(shot, "generate")
    if g != "done":
        return "chimera.generate_video", "Generate Video", "FILE_MOVIE", f"H3 · {shot.h3_mode.upper()}"
    return "chimera.open_edit", "Review in Edit", "SEQUENCE", "Video is in the _EDIT scene"


def warnings(shot, fps):
    out = []
    if shot.duration_s > H3_MAX_S:
        out.append(f"{shot.duration_s:.1f}s exceeds one H3 generation ({H3_MAX_S:.0f}s); "
                   "segmenting is not implemented yet")
    elif shot.duration_s > MOTION_MAX_S:
        out.append(f"{shot.duration_s:.1f}s exceeds one Kimodo call (~{MOTION_MAX_S:.0f}s)")
    if len(shot.characters) > 1:
        out.append("Multiple characters are generated independently (no interaction)")
    return out


def wrap(text, width_px, char_px=7.0):
    """Greedy word wrap for labels (Blender has no multi-line label)."""
    n = max(12, int(width_px / char_px))
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > n:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines
