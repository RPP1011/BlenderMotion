"""Kimodo BVH takes -> actions on the user's rig (headless Blender).

    blender -b --factory-startup --python server/workers/retarget.py -- <spec.json>

Spec (written by server/pipeline.py):

  {"fps": 24, "frames": 96, "out_blend": ".../motion.blend", "out_json": ".../actions.json",
   "characters": {"hero": {
       "start_matrix_world": [[4x4]] | null,   # START empty; null = world origin
       "scale": 0.5,                            # soma_hip / rig_hip (the add-on's)
       "rig": {"object": "HeroRig", "matrix_world": [[4x4]],
               "bonemap": {canonical_joint: bone},
               "bones": [{"name", "parent", "matrix_local": [[4x4]], "length", "rotation_mode"}]} | null,
       "library": {"path": "/abs/hero.blend", "object": "HeroRig"} | null,
       "takes": [{"bvh": ".../take_0.bvh", "variant": 0}]}}}

Writes out_blend (only the actions, with fake users) and out_json
([{char, action, variant}]); prints RETARGET_OK.

Method, per take:
1. Import the BVH (axis_forward -Z, axis_up Y, global_scale 0.01 because the
   BVH is in cm, use_fps_scale so Kimodo's 30 fps lands on the scene's 24).
   Blender then has the SOMA skeleton facing -Y with its left on +X.
2. Place it: object matrix = START @ Rz(180) @ S(1/scale). Rz(180) takes
   Blender's -Y facing to START's +Y facing (the add-on's canonical frame is
   (-x, z, y) of START-local); 1/scale undoes the add-on's soma/rig scaling,
   so the source skeleton is the rig's size and hips translation needs no
   further scaling.
3. Transfer onto the target armature (rebuilt from the rest data the add-on
   sent, or appended from the shot's library rig), world space, per mapped bone:
       R_tgt(f) = R_src(f) . R_src_rest^-1 . Q . G . R_tgt_rest
   G is a yaw that turns the target's rest facing onto the source's (from the
   hip joints), Q is the swing that points the target's rest limb (joint ->
   mapped child joint) along the source's rest limb, so A-pose rigs take
   T-pose motion. The pelvis also takes the source hips' world position. The
   world rotations are converted to each pose bone's basis through the
   hierarchy and keyed in the bone's rotation mode.
4. If the rig is a SOMA skeleton (every BVH bone exists on the rig), all bones
   map 1:1 and Q is identity. If no rig data was sent at all, the BVH action is
   written as is (SOMA bone names, canonical placement) and a warning printed.
"""

import importlib.util
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
BONEMAP_PY = os.path.join(HERE, "..", "..", "addon", "chimera_director", "bonemap.py")

# Canonical joint -> its child joint, used to align limb directions (Q).
CHILD = {
    "l_clavicle": "l_shoulder", "l_shoulder": "l_elbow", "l_elbow": "l_wrist",
    "r_clavicle": "r_shoulder", "r_shoulder": "r_elbow", "r_elbow": "r_wrist",
    "l_hip": "l_knee", "l_knee": "l_ankle", "l_ankle": "l_toe",
    "r_hip": "r_knee", "r_knee": "r_ankle", "r_ankle": "r_toe",
}


def log(msg):
    print(f"[retarget] {msg}", flush=True)


def load_bonemap_module():
    spec = importlib.util.spec_from_file_location("chimera_bonemap", BONEMAP_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rq(m):
    """Rotation of a (possibly uniformly scaled) matrix as a unit quaternion."""
    return m.to_3x3().normalized().to_quaternion()


def depth(bone):
    d = 0
    while bone.parent:
        d += 1
        bone = bone.parent
    return d


# -- target armature -----------------------------------------------------------------

def build_rig(rig):
    """Recreate an armature with the rig's exact rest pose (bone names, parents,
    rest matrices, rotation modes). Actions keyed on it drive the real rig."""
    arm = bpy.data.armatures.new(rig.get("object", "Rig") + "_rest")
    obj = bpy.data.objects.new(rig.get("object", "Rig"), arm)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bones = rig["bones"]
    for b in bones:
        eb = arm.edit_bones.new(b["name"])
        eb.head, eb.tail = (0, 0, 0), (0, max(float(b.get("length") or 0.1), 1e-3), 0)
        eb.matrix = Matrix(b["matrix_local"])
    for b in bones:
        if b.get("parent") and b["parent"] in arm.edit_bones:
            arm.edit_bones[b["name"]].parent = arm.edit_bones[b["parent"]]
    bpy.ops.object.mode_set(mode="OBJECT")
    for b in bones:
        obj.pose.bones[b["name"]].rotation_mode = b.get("rotation_mode") or "QUATERNION"
    if rig.get("matrix_world"):
        obj.matrix_world = Matrix(rig["matrix_world"])
    return obj


def append_library_rig(lib, matrix_world=None):
    path = bpy.path.abspath(lib["path"])
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        if lib["object"] not in src.objects:
            raise KeyError(f"{lib['object']} not in {path}")
        dst.objects = [lib["object"]]
    obj = dst.objects[0]
    bpy.context.scene.collection.objects.link(obj)
    if obj.type != "ARMATURE":
        raise TypeError(f"{lib['object']} in {path} is not an armature")
    if matrix_world:
        obj.matrix_world = Matrix(matrix_world)
    obj.animation_data_clear()
    return obj


# -- source ------------------------------------------------------------------------

def import_bvh(path, place):
    before = set(bpy.data.objects)
    bpy.ops.import_anim.bvh(filepath=path, axis_forward="-Z", axis_up="Y", global_scale=0.01,
                            use_fps_scale=True, update_scene_fps=False, update_scene_duration=False,
                            rotate_mode="NATIVE", frame_start=1)
    new = [o for o in bpy.data.objects if o not in before and o.type == "ARMATURE"]
    if not new:
        raise RuntimeError(f"BVH import produced no armature: {path}")
    src = new[0]
    src.matrix_world = place @ src.matrix_world
    return src


# -- transfer ----------------------------------------------------------------------

def facing(obj, bmap):
    """Rest facing (unit XY vector) from the hip joints: facing = left x up."""
    lh, rh = bmap.get("l_hip"), bmap.get("r_hip")
    if not lh or not rh or lh not in obj.data.bones or rh not in obj.data.bones:
        return None
    left = obj.matrix_world @ obj.data.bones[lh].head_local - obj.matrix_world @ obj.data.bones[rh].head_local
    f = left.cross(Vector((0, 0, 1)))
    f.z = 0
    return f.normalized() if f.length > 1e-6 else None


def yaw_between(a, b):
    if a is None or b is None:
        return Quaternion()
    ang = math.atan2(b.y, b.x) - math.atan2(a.y, a.x)
    return Quaternion((0, 0, 1), ang)


def head_world(obj, bone):
    return obj.matrix_world @ obj.data.bones[bone].head_local


def plan_pairs(src, tgt, tgt_map, src_map):
    """[(src_bone, tgt_bone, joint or None)] and whether the 1:1 SOMA path applies."""
    src_names = {b.name for b in src.data.bones}
    tgt_names = {b.name for b in tgt.data.bones}
    if src_names and src_names <= tgt_names:
        return [(n, n, None) for n in src_names], True
    pairs = []
    for joint, tb in tgt_map.items():
        sb = src_map.get(joint)
        if sb and tb and sb in src_names and tb in tgt_names:
            pairs.append((sb, tb, joint))
    return pairs, False


def transfer(src, tgt, pairs, soma, tgt_map, src_map, frames, root_joint_src, root_tgt):
    sw, tw = src.matrix_world, tgt.matrix_world
    tw_rot_inv = rq(tw).inverted()
    tw_inv = tw.inverted()
    g = Quaternion() if soma else yaw_between(facing(tgt, tgt_map), facing(src, src_map))
    offset, src_rest_inv = {}, {}
    for sb, tb, joint in pairs:
        src_rest = rq(sw @ src.data.bones[sb].matrix_local)
        tgt_rest = rq(tw @ tgt.data.bones[tb].matrix_local)
        q = Quaternion()
        child = CHILD.get(joint) if joint else None
        if child and (src_map.get(child) or "") in src.data.bones and (tgt_map.get(child) or "") in tgt.data.bones:
            sd = head_world(src, src_map[child]) - head_world(src, sb)
            td = g @ (head_world(tgt, tgt_map[child]) - head_world(tgt, tb))
            if sd.length > 1e-6 and td.length > 1e-6:
                q = td.normalized().rotation_difference(sd.normalized())
        offset[tb] = q @ g @ tgt_rest
        src_rest_inv[tb] = src_rest.inverted()
    by_tgt = {tb: sb for sb, tb, _ in pairs}
    order = sorted(tgt.data.bones, key=depth)
    rest_rel = {}
    for b in order:
        rest_rel[b.name] = (b.parent.matrix_local.inverted() @ b.matrix_local) if b.parent else b.matrix_local.copy()
    scene = bpy.context.scene
    keys = {b.name: [] for b in order if b.name in by_tgt}
    for f in range(frames):
        scene.frame_set(1 + f)
        pose = {}
        for b in order:
            base = (pose[b.parent.name] if b.parent else Matrix()) @ rest_rel[b.name]
            sb = by_tgt.get(b.name)
            if sb is None:
                pose[b.name] = base
                continue
            r_src = rq(sw @ src.pose.bones[sb].matrix)
            r_world = r_src @ src_rest_inv[b.name] @ offset[b.name]
            r_pose = tw_rot_inv @ r_world
            rot = rq(base).inverted() @ r_pose
            loc = Vector()
            if b.name == root_tgt and root_joint_src:
                hip_world = sw @ src.pose.bones[root_joint_src].head
                loc = base.inverted() @ (tw_inv @ hip_world)
            pose[b.name] = base @ Matrix.Translation(loc) @ rot.to_matrix().to_4x4()
            keys[b.name].append((f, rot, loc))
    return keys


def write_keys(tgt, keys, root_tgt, action_name):
    tgt.animation_data_clear()
    for name, frames in keys.items():
        pb = tgt.pose.bones[name]
        mode = pb.rotation_mode
        prev = None
        for f, rot, loc in frames:
            if mode == "QUATERNION":
                if prev is not None and prev.dot(rot) < 0:
                    rot = -rot
                pb.rotation_quaternion = rot
                prev = rot.copy()
                pb.keyframe_insert("rotation_quaternion", frame=f, group=name)
            elif mode == "AXIS_ANGLE":
                axis, ang = rot.to_axis_angle()
                pb.rotation_axis_angle = (ang, *axis)
                pb.keyframe_insert("rotation_axis_angle", frame=f, group=name)
            else:
                e = rot.to_euler(mode, prev) if prev is not None else rot.to_euler(mode)
                pb.rotation_euler = e
                prev = e
                pb.keyframe_insert("rotation_euler", frame=f, group=name)
            if name == root_tgt:
                pb.location = loc
                pb.keyframe_insert("location", frame=f, group=name)
    act = tgt.animation_data.action
    act.name = action_name
    act.use_fake_user = True
    return act


def run(spec):
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = int(spec.get("fps", 24)), 1.0
    frames = int(spec["frames"])
    bm = load_bonemap_module()
    actions, entries = [], []
    for cid, c in spec["characters"].items():
        start = Matrix(c["start_matrix_world"]) if c.get("start_matrix_world") else Matrix()
        scale = float(c.get("scale") or 1.0)
        place = start @ Matrix.Rotation(math.pi, 4, "Z") @ Matrix.Diagonal((1 / scale,) * 3 + (1,))
        rig = c.get("rig") or {}
        tgt = None
        if rig.get("bones"):
            tgt = build_rig(rig)
        elif c.get("library"):
            tgt = append_library_rig(c["library"], rig.get("matrix_world"))
        tgt_map = (rig.get("bonemap") or {}) if tgt else {}
        if tgt is not None and not tgt_map:
            tgt_map = bm.guess([b.name for b in tgt.data.bones])
        for take in c["takes"]:
            src = import_bvh(take["bvh"], place)
            name = f"mo_{cid}_{take['variant']}"
            if tgt is None:
                log(f"WARNING {cid}: no rig data; writing the SOMA action unretargeted")
                act = src.animation_data.action
                act.name = name
                act.use_fake_user = True
            else:
                src_map = bm.guess([b.name for b in src.data.bones])
                pairs, soma = plan_pairs(src, tgt, tgt_map, src_map)
                if not pairs:
                    raise RuntimeError(f"{cid}: no bones in common between the bone map and the BVH skeleton")
                root_tgt = src_map.get("pelvis") if soma else tgt_map.get("pelvis")
                root_src = src_map.get("pelvis")
                log(f"{cid} v{take['variant']}: {len(pairs)} bones ({'SOMA 1:1' if soma else 'bone map'}), "
                    f"root {root_src} -> {root_tgt}")
                keys = transfer(src, tgt, pairs, soma, tgt_map, src_map, frames, root_src, root_tgt)
                act = write_keys(tgt, keys, root_tgt, name)
                tgt.animation_data_clear()
                old = src.animation_data.action if src.animation_data else None
                bpy.data.objects.remove(src)
                if old is not None and old.users == 0:
                    bpy.data.actions.remove(old)
            actions.append(act)
            entries.append({"char": cid, "action": act.name, "variant": int(take["variant"])})
    bpy.data.libraries.write(spec["out_blend"], set(actions), fake_user=True)
    with open(spec["out_json"], "w") as f:
        json.dump(entries, f)
    log(f"wrote {len(entries)} action(s) to {spec['out_blend']}")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    with open(argv[0]) as f:
        spec = json.load(f)
    run(spec)
    print("RETARGET_OK", flush=True)


if __name__ == "__main__":
    main()
