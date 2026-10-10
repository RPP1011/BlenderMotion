"""Snapshots for headless workers and per-shot scene hashing (docs §4.3, §5.3).

Hash scope differs from the doc in one way: the doc hashes the camera's
world matrix *per frame*. That requires frame_set() over the whole shot,
which is slow and re-enters depsgraph handlers. Here the camera contributes
its static matrix plus its action's keyframes and its constraints'
targets. Camera motion driven by drivers or parented to an animated object
other than via keys is NOT detected; hit "Refresh hash" after such edits.
"""

import hashlib
import json
import os

import bpy

from . import compat, constraints, scene_io


def _r(v, nd=4):
    return [round(x, nd) for x in v]


def _action_keys(action):
    if action is None:
        return None
    out = []
    for fc in sorted(compat.iter_fcurves(action), key=lambda f: (f.data_path, f.array_index)):
        out.append([fc.data_path, fc.array_index,
                    [_r(kp.co) + _r(kp.handle_left) + _r(kp.handle_right) for kp in fc.keyframe_points]])
    return out


def _obj_state(obj):
    if obj is None:
        return None
    ad = obj.animation_data
    return {
        "m": [_r(row) for row in obj.matrix_world],
        "parent": obj.parent.name if obj.parent else None,
        "action": _action_keys(ad.action if ad else None),
        "cons": [[c.type, getattr(getattr(c, "target", None), "name", None)] for c in obj.constraints],
    }


def scene_hash(scene, shot):
    """Hash of everything stages 4-7 depend on for this shot."""
    cam = bpy.data.objects.get(scene_io.cam_name(shot.name))
    state = {
        "range": scene_io.shot_range(scene, shot.name),
        "camera": _obj_state(cam),
        "lens": [cam.data.lens, cam.data.sensor_width, cam.data.clip_start, cam.data.clip_end]
        if cam and cam.type == "CAMERA" else None,
        "res": [shot.resolution_x, shot.resolution_y],
        "chars": [],
    }
    strips = {c.char_id: s for c, s in scene_io.shot_strips(shot)}
    for char in shot.characters:
        rig = char.rig_object
        strip = strips.get(char.char_id)
        state["chars"].append({
            "id": char.char_id,
            "rig": _obj_state(rig),
            "props": {k: str(rig[k]) for k in rig.keys()} if rig else None,
            "strip": [strip.action.name if strip.action else None, round(strip.frame_start, 3),
                      round(strip.frame_end, 3), round(strip.scale, 4), round(strip.repeat, 4)]
            if strip else None,
            "motion": _action_keys(strip.action) if strip else None,
        })
    return _digest(state)


def constraints_hash(shot):
    state = []
    for obj in sorted(constraints.helpers(shot.name), key=lambda o: o.name):
        entry = [obj.name, obj.get("chimera_role"), obj.get("chimera_char"),
                 obj.get("chimera_frame"), obj.get("chimera_joint"), _r(sum(map(list, obj.matrix_world), []))]
        if obj.type == "CURVE":
            for spline in obj.data.splines:
                entry.append([_r(p.co) for p in spline.bezier_points] + [_r(p.co) for p in spline.points])
        state.append(entry)
    return _digest(state)


def _digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


def take(project, shot_id, scene_hash_value):
    """Copy-save the current file for a worker. Never touches the live file."""
    root = bpy.path.abspath(project.root)
    if not root:
        raise RuntimeError("Set the project root first")
    folder = os.path.join(root, "snapshots")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{shot_id}_{scene_hash_value[:12]}.blend")
    if not os.path.exists(path):
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, compress=False, relative_remap=True)
    return path
