"""Spatial targets authored in Blender -> motion-model constraints (docs §4.1a).

Helper objects live in `SHOT_<id>` and are identified by custom properties:
  chimera_role  = "start" | "path" | "waypoint" | "effector"
  chimera_shot  = shot id
  chimera_char  = character id
  chimera_frame = frame relative to the shot's in-point (waypoint/effector)
  chimera_joint = effector joint, e.g. "right_hand" (effector)

Canonical frame. The START empty's local axes are (x right, +Y facing, z up).
The motion model's canonical frame is assumed Y-up with the character facing
+Z at frame 0 (right-handed), which makes +X the character's left:

    canonical = (-x_local, z_local, y_local) * scale

UNVERIFIED against Kimodo's actual convention; it is isolated in
`to_canonical` so one function changes if it is wrong.

Scale. Constraints are solved on the model's skeleton, so distances authored
on your rig are scaled by soma_hip_height / rig_hip_height. (The architecture
doc says rig/soma; that is inverted: a rig twice as tall should cover twice
the ground for the same motion, so its targets shrink going into SOMA space.)
The inverse scale is applied when the worker places the clip back into world.
"""

import math

import bpy
from mathutils import Vector

from . import scene_io

ROLES = ("start", "path", "waypoint", "effector")
EFFECTOR_JOINTS = [
    ("right_hand", "Right hand", ""), ("left_hand", "Left hand", ""),
    ("right_foot", "Right foot", ""), ("left_foot", "Left foot", ""),
    ("head", "Head", ""), ("pelvis", "Pelvis", ""),
]
HIP_BONE_GUESSES = ("pelvis", "hips", "hip", "mixamorig:hips", "def-spine", "root")


def helpers(shot_id, char_id=None, role=None):
    coll = bpy.data.collections.get(scene_io.coll_name(shot_id))
    if coll is None:
        return []
    out = []
    for obj in coll.all_objects:
        if obj.get("chimera_shot") != shot_id:
            continue
        if char_id is not None and obj.get("chimera_char") != char_id:
            continue
        if role is not None and obj.get("chimera_role") != role:
            continue
        out.append(obj)
    return out


def rig_hip_height(rig):
    if rig is None or rig.type != "ARMATURE":
        return None
    for bone in rig.data.bones:
        if bone.name.lower() in HIP_BONE_GUESSES:
            return (rig.matrix_world @ bone.head_local).z - rig.matrix_world.translation.z
    return None


def to_canonical(start_inv, world_co, scale):
    p = start_inv @ Vector(world_co)
    return [-p.x * scale, p.z * scale, p.y * scale]


def heading_in_start(start_inv, world_dir):
    """Yaw (radians) of a world direction in the canonical frame; 0 = facing +Z."""
    d = start_inv.to_3x3() @ Vector(world_dir)
    return math.atan2(-d.x, d.y)


def sample_curve(obj, n):
    """n world-space points along the first spline of a curve, uniform in arc length."""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    mesh = ev.to_mesh()
    try:
        pts = [obj.matrix_world @ v.co for v in mesh.vertices]
        # to_mesh() orders vertices along the spline; keep connected run only.
        edges = {tuple(sorted(e.vertices)) for e in mesh.edges}
        run = [0]
        for i in range(1, len(pts)):
            if (i - 1, i) not in edges:
                break
            run.append(i)
        pts = [pts[i] for i in run]
    finally:
        ev.to_mesh_clear()
    if len(pts) < 2:
        return [pts[0].copy()] * n if pts else []
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + (b - a).length)
    total = cum[-1] or 1.0
    out, j = [], 0
    for k in range(n):
        target = total * k / max(1, n - 1)
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        seg = cum[j + 1] - cum[j] or 1.0
        t = (target - cum[j]) / seg
        out.append(pts[j].lerp(pts[j + 1], min(max(t, 0.0), 1.0)))
    return out


def export(scene, project, shot, soma_hip_height):
    """Build the per-character constraint payload for POST /motion."""
    n_frames = scene_io.frames_for(shot, project.fps)
    chars = {}
    warnings = []
    for char in shot.characters:
        cid = char.char_id
        starts = helpers(shot.name, cid, "start")
        if not starts:
            chars[cid] = {"start_matrix_world": None, "scale": 1.0, "constraints": []}
            continue
        start = starts[0]
        rig_hip = rig_hip_height(char.rig_object)
        if rig_hip is None or rig_hip <= 1e-4:
            if char.rig_object is not None:
                warnings.append(f"{cid}: no hip bone found; constraints unscaled")
            scale = 1.0
        else:
            scale = soma_hip_height / rig_hip
        inv = start.matrix_world.inverted_safe()
        cons = []

        for path in helpers(shot.name, cid, "path"):
            if path.type != "CURVE":
                continue
            pts = sample_curve(path, n_frames)
            if not pts:
                continue
            canon = [to_canonical(inv, p, scale) for p in pts]
            heads = []
            for i in range(len(pts)):
                a, b = pts[max(i - 1, 0)], pts[min(i + 1, len(pts) - 1)]
                d = b - a
                heads.append(heading_in_start(inv, d) if d.length > 1e-6 else (heads[-1] if heads else 0.0))
            cons.append({
                "type": "root2d", "mode": "dense",
                "frames": list(range(len(pts))),
                "xz": [[c[0], c[2]] for c in canon],
                "heading": heads,
            })
            break  # one path per character

        wps = sorted(helpers(shot.name, cid, "waypoint"), key=lambda o: o.get("chimera_frame", 0))
        if wps:
            entry = {"type": "root2d", "mode": "sparse", "frames": [], "xz": [], "heading": []}
            for wp in wps:
                c = to_canonical(inv, wp.matrix_world.translation, scale)
                entry["frames"].append(int(wp.get("chimera_frame", 0)))
                entry["xz"].append([c[0], c[2]])
                fwd = wp.matrix_world.to_3x3() @ Vector((0, 1, 0))
                entry["heading"].append(heading_in_start(inv, fwd) if wp.get("chimera_use_heading") else None)
            cons.append(entry)

        for eff in helpers(shot.name, cid, "effector"):
            cons.append({
                "type": "end_effector",
                "joint": eff.get("chimera_joint", "right_hand"),
                "frame": int(eff.get("chimera_frame", 0)),
                "position": to_canonical(inv, eff.matrix_world.translation, scale),
            })

        for c in cons:
            for f in c.get("frames", [c.get("frame", 0)]):
                if not 0 <= f < n_frames:
                    warnings.append(f"{cid}: {c['type']} frame {f} outside shot (0..{n_frames - 1})")

        chars[cid] = {
            "start_matrix_world": [list(row) for row in start.matrix_world],
            "scale": scale,
            "constraints": cons,
        }
    return {"characters": chars, "frames": n_frames, "continuity": shot.continuity}, warnings
