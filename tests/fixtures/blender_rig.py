"""Blender helper for the retarget tests (run with blender -b --factory-startup).

    --python blender_rig.py -- dump <rig.json> [soma <take.bvh>]
        Build the demo stick rig (or, with `soma`, a rig imported from a SOMA
        BVH) and write the rest payload the add-on sends (constraints.rig_payload).
    --python blender_rig.py -- check <rig.json> <motion.blend> <actions.json> <out.json>
        Rebuild the rig, play each action and record world positions per frame.
"""
import json
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(REPO, "tools"))


def bonemap_guess(names):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bm", os.path.join(REPO, "addon", "chimera_director", "bonemap.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.guess(names)


def payload(rig):
    return {"object": rig.name, "matrix_world": [list(r) for r in rig.matrix_world],
            "bonemap": bonemap_guess([b.name for b in rig.data.bones]),
            "bones": [{"name": b.name, "parent": b.parent.name if b.parent else None,
                       "matrix_local": [list(r) for r in b.matrix_local], "length": b.length,
                       "rotation_mode": rig.pose.bones[b.name].rotation_mode} for b in rig.data.bones]}


def build(rig_json):
    with open(rig_json) as f:
        p = json.load(f)
    arm = bpy.data.armatures.new("A")
    rig = bpy.data.objects.new(p["object"], arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    from mathutils import Matrix
    for b in p["bones"]:
        eb = arm.edit_bones.new(b["name"])
        eb.head, eb.tail = (0, 0, 0), (0, b["length"], 0)
        eb.matrix = Matrix(b["matrix_local"])
    for b in p["bones"]:
        if b["parent"]:
            arm.edit_bones[b["name"]].parent = arm.edit_bones[b["parent"]]
    bpy.ops.object.mode_set(mode="OBJECT")
    for b in p["bones"]:
        rig.pose.bones[b["name"]].rotation_mode = b["rotation_mode"]
    rig.matrix_world = Matrix(p["matrix_world"])
    return rig, p


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if argv[0] == "dump":
        if len(argv) > 2 and argv[2] == "soma":
            bpy.ops.import_anim.bvh(filepath=argv[3], axis_forward="-Z", axis_up="Y", global_scale=0.01)
            rig = bpy.context.selected_objects[0]
            rig.animation_data_clear()
            rig.name = "SomaRig"
            for pb in rig.pose.bones:
                pb.rotation_mode = "QUATERNION"
        else:
            import demo_scene
            rig = demo_scene.make_rig()
            rig.location = (2.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        with open(argv[1], "w") as f:
            json.dump(payload(rig), f)
    elif argv[0] == "check":
        rig, p = build(argv[1])
        with bpy.data.libraries.load(argv[2]) as (src, dst):
            dst.actions = list(src.actions)
        with open(argv[3]) as f:
            entries = json.load(f)
        bm = p["bonemap"]
        res = {}
        for e in entries:
            act = bpy.data.actions[e["action"]]
            ad = rig.animation_data or rig.animation_data_create()
            ad.action = act
            if hasattr(ad, "action_slot") and getattr(act, "slots", None):
                ad.action_slot = act.slots[0]
            frames = []
            lo, hi = act.frame_range
            for f in range(int(lo), int(hi) + 1):
                bpy.context.scene.frame_set(f)
                mw = rig.matrix_world
                pos = {j: list(mw @ rig.pose.bones[b].head) for j, b in bm.items() if b in rig.pose.bones}
                frames.append(pos)
            res[e["action"]] = {"range": [lo, hi], "frames": frames}
        with open(argv[4], "w") as f:
            json.dump(res, f)
    print("BLENDER_RIG_OK", flush=True)


main()
