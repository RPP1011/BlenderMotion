"""Scene conventions (docs §3.2): collections, markers, cameras, rigs, actions, VSE.

Two deliberate deviations from §3.2, both forced by "one scene, shared timeline":

* Motion lives on the NLA, not in `animation_data.action`. One rig appears in
  many shots, so each shot's selected variant is an NLA strip on track
  `SHOT_<id>` starting at the `<id>_in` marker. Picking a variant swaps the
  strip's action. Edit motion by tweaking the strip (Tab in the NLA editor).
* The assembled edit lives in a separate scene `<scene>_EDIT`. A VSE with
  strips in the animation scene turns on sequencer post-processing, and every
  render (including the workers' passes) would output the edit instead of the
  3D scene.

Object names are global in a .blend, so per-shot helpers are named
`<KIND>_<shot>_<char>` and identified by custom properties, not by name.
"""

import os

import bpy

from . import compat

SHOT_PREFIX = "SHOT_"
CHAR_COLLECTION = "CHARACTERS"


# -- naming --------------------------------------------------------------------

def coll_name(shot_id):
    return SHOT_PREFIX + shot_id


def cam_name(shot_id):
    return f"CAM_{shot_id}"


def action_name(shot_id, char_id, n):
    return f"MO_{shot_id}_{char_id}_v{n}"


def frames_for(shot, fps):
    return max(1, round(shot.duration_s * fps))


def h3_frame_count(n):
    """Smallest 17k+5 >= n: H3's frame grid at 24 fps (extend, never trim)."""
    return 5 if n <= 5 else n + (17 - (n - 5) % 17) % 17


# -- collections & markers -----------------------------------------------------

def ensure_child_collection(parent, name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in parent.children:
        parent.children.link(coll)
    return coll


def ensure_shot_collection(scene, shot_id):
    return ensure_child_collection(scene.collection, coll_name(shot_id))


def marker(scene, name):
    return scene.timeline_markers.get(name)


def shot_range(scene, shot_id):
    """(in, out) frames from markers; out is exclusive. None if markers missing."""
    m_in, m_out = marker(scene, f"{shot_id}_in"), marker(scene, f"{shot_id}_out")
    if m_in is None or m_out is None:
        return None
    return m_in.frame, m_out.frame


def set_marker(scene, name, frame):
    m = marker(scene, name) or scene.timeline_markers.new(name, frame=frame)
    m.frame = frame
    return m


def layout_shots(scene, project):
    """Pack shots end to end on the timeline in `order`, from their durations."""
    frame = project.start_frame
    for shot in sorted(project.shots, key=lambda s: s.order):
        n = frames_for(shot, project.fps)
        set_marker(scene, f"{shot.name}_in", frame)
        set_marker(scene, f"{shot.name}_out", frame + n)
        cam = bpy.data.objects.get(cam_name(shot.name))
        if cam is not None:
            marker(scene, f"{shot.name}_in").camera = cam
        _move_nla_strips(shot, frame)
        frame += n
    if project.shots:
        scene.frame_start = project.start_frame
        scene.frame_end = frame - 1
    scene.render.fps = project.fps
    scene.render.fps_base = 1.0


def durations_from_markers(scene, project):
    """Markers are authoritative for timing once they exist (user may drag them)."""
    for shot in project.shots:
        rng = shot_range(scene, shot.name)
        if rng and rng[1] > rng[0]:
            dur = (rng[1] - rng[0]) / project.fps
            if abs(dur - shot.duration_s) > 1e-6:
                shot.duration_s = dur


def ensure_shot(scene, project, shot):
    ensure_shot_collection(scene, shot.name)
    if shot_range(scene, shot.name) is None:
        layout_shots(scene, project)
    ensure_camera(scene, shot)


def remove_shot(scene, shot_id):
    for suffix in ("_in", "_out"):
        m = marker(scene, shot_id + suffix)
        if m:
            scene.timeline_markers.remove(m)
    coll = bpy.data.collections.get(coll_name(shot_id))
    if coll is not None:
        for obj in list(coll.objects):
            if obj.get("chimera_shot") == shot_id or obj.type == "CAMERA":
                bpy.data.objects.remove(obj)
        bpy.data.collections.remove(coll)
    for obj in scene.objects:
        ad = obj.animation_data
        if obj.type == "ARMATURE" and ad:
            track = ad.nla_tracks.get(coll_name(shot_id))
            if track:
                ad.nla_tracks.remove(track)


# -- camera --------------------------------------------------------------------

def ensure_camera(scene, shot):
    name = cam_name(shot.name)
    cam = bpy.data.objects.get(name)
    if cam is None:
        data = bpy.data.cameras.new(name)
        data.lens = 35.0
        cam = bpy.data.objects.new(name, data)
        cam.location = (0.0, -8.0, 1.6)
        cam.rotation_euler = (1.5708, 0.0, 0.0)
        cam["chimera_shot"] = shot.name
    coll = ensure_shot_collection(scene, shot.name)
    if cam.name not in coll.objects:
        coll.objects.link(cam)
    m = marker(scene, f"{shot.name}_in")
    if m is not None:
        m.camera = cam
    if scene.camera is None:
        scene.camera = cam
    return cam


# -- rigs ----------------------------------------------------------------------

def link_rig(scene, project_root, ref):
    """Link `<file.blend>#<Object>` and make a library override. Returns the armature."""
    if "#" not in ref:
        raise ValueError(f"rig reference must be '<file.blend>#<Object>', got {ref!r}")
    rel, obj_name = ref.split("#", 1)
    path = rel if os.path.isabs(rel) else os.path.join(bpy.path.abspath(project_root), rel)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    lib_path = os.path.normcase(os.path.abspath(path))
    for obj in bpy.data.objects:  # already overridden?
        ref_id = obj.override_library.reference if obj.override_library else None
        if (ref_id and ref_id.name == obj_name and ref_id.library
                and os.path.normcase(os.path.abspath(bpy.path.abspath(ref_id.library.filepath))) == lib_path):
            return obj
    with bpy.data.libraries.load(path, link=True) as (src, dst):
        if obj_name not in src.objects:
            raise KeyError(f"{obj_name!r} not found in {path}")
        dst.objects = [obj_name]
    linked = dst.objects[0]
    chars = ensure_child_collection(scene.collection, CHAR_COLLECTION)
    chars.objects.link(linked)
    override = linked.override_hierarchy_create(scene, bpy.context.view_layer, do_fully_editable=True)
    if override is not None and override is not linked:
        if linked.name in chars.objects:
            chars.objects.unlink(linked)
        if override.name not in chars.objects:
            for c in list(override.users_collection):
                c.objects.unlink(override)
            chars.objects.link(override)
    return override or linked


# -- motion (NLA) --------------------------------------------------------------

def variant_actions(shot_id, char_id):
    """{n: action} for all variants of a shot/char."""
    prefix = f"MO_{shot_id}_{char_id}_v"
    out = {}
    for act in bpy.data.actions:
        if act.name.startswith(prefix) and act.name[len(prefix):].isdigit():
            out[int(act.name[len(prefix):])] = act
    return dict(sorted(out.items()))


def next_variant_index(shot_id, char_ids):
    used = [n for c in char_ids for n in variant_actions(shot_id, c)]
    return max(used) + 1 if used else 0


def import_actions(blend_path, entries, shot_id):
    """Append actions from a worker snapshot and rename them to MO_<shot>_<char>_v<n>.

    `entries`: [{"char": id, "action": name_in_blend, "variant": i}]. Variants
    are appended after existing ones; existing actions (and manual edits) are
    never overwritten. Returns {char: [n, ...]}.
    """
    names = [e["action"] for e in entries]
    with bpy.data.libraries.load(blend_path, link=False) as (src, dst):
        missing = [n for n in names if n not in src.actions]
        if missing:
            raise KeyError(f"actions not in {blend_path}: {missing}")
        dst.actions = names
    base = next_variant_index(shot_id, {e["char"] for e in entries})
    result = {}
    for entry, act in zip(entries, dst.actions):
        n = base + int(entry.get("variant", 0))
        act.name = action_name(shot_id, entry["char"], n)
        act.use_fake_user = True
        act["chimera_shot"] = shot_id
        act["chimera_char"] = entry["char"]
        act["chimera_import_hash"] = action_hash(act)
        result.setdefault(entry["char"], []).append(n)
    return result


def action_hash(action):
    import hashlib
    h = hashlib.sha256()
    for fc in sorted(compat.iter_fcurves(action), key=lambda f: (f.data_path, f.array_index)):
        h.update(f"{fc.data_path}[{fc.array_index}]".encode())
        for kp in fc.keyframe_points:
            h.update(("%.4f,%.5f;" % (kp.co[0], kp.co[1])).encode())
    return h.hexdigest()


def assign_variant(scene, shot, n):
    """Put variant n of every character on the shot's NLA track."""
    rng = shot_range(scene, shot.name)
    start = rng[0] if rng else scene.frame_start
    assigned = 0
    for char in shot.characters:
        rig = char.rig_object
        act = variant_actions(shot.name, char.char_id).get(n)
        if rig is None or act is None:
            continue
        ad = rig.animation_data or rig.animation_data_create()
        ad.action = None  # an active action would override the NLA
        track = ad.nla_tracks.get(coll_name(shot.name)) or ad.nla_tracks.new()
        track.name = coll_name(shot.name)
        for strip in list(track.strips):
            track.strips.remove(strip)
        strip = track.strips.new(act.name, int(start), act)
        compat.assign_strip_slot(strip)
        strip.extrapolation = "NOTHING"
        strip.blend_type = "REPLACE"
        assigned += 1
    shot.selected_variant = n
    return assigned


def shot_strips(shot):
    for char in shot.characters:
        rig = char.rig_object
        if rig and rig.animation_data:
            track = rig.animation_data.nla_tracks.get(coll_name(shot.name))
            if track:
                for strip in track.strips:
                    yield char, strip


def _move_nla_strips(shot, frame):
    for _char, strip in shot_strips(shot):
        delta = frame - strip.frame_start
        if abs(delta) > 1e-6:
            # Move end first when moving right so start never passes end.
            if delta > 0:
                strip.frame_end += delta
                strip.frame_start += delta
            else:
                strip.frame_start += delta
                strip.frame_end += delta


# -- VSE -----------------------------------------------------------------------

def edit_scene(scene):
    name = scene.name + "_EDIT"
    edit = bpy.data.scenes.get(name)
    if edit is None:
        edit = bpy.data.scenes.new(name)
    edit.render.fps = scene.render.fps
    edit.render.fps_base = scene.render.fps_base
    edit.render.resolution_x = scene.render.resolution_x
    edit.render.resolution_y = scene.render.resolution_y
    edit.frame_start, edit.frame_end = scene.frame_start, scene.frame_end
    if edit.sequence_editor is None:
        edit.sequence_editor_create()
    return edit


def place_strip(edit, name, kind, filepath, channel, frame, mute=False):
    strips = compat.vse_strips(edit.sequence_editor)
    old = strips.get(name)
    if old is not None:
        strips.remove(old)
    if kind == "movie":
        strip = strips.new_movie(name, filepath, channel, int(frame))
    else:
        strip = strips.new_sound(name, filepath, channel, int(frame))
    strip.mute = mute
    return strip
