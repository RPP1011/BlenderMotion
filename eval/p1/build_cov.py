"""Coverage builder: one two-person Kimodo performance, one set, several cameras.

blender -b --python build_cov.py -- <set> <out.blend> <width> <height> <a.bvh> <b.bvh> [plate.obj cam.json]

set: grey   ground plane + a facade block where the plate's facade sits (Workbench playblast)
     plate  MoGe mesh of the set image, unlit image texture, placed at the plate camera (EEVEE playblast)
The two BVHs share Kimodo's world frame (root2d constraints put them face to face along +X).
The pair is rotated by PHI about the handshake point and moved to MEET so every camera looks
into the plate's field of view (the facade is at +Y). Cameras: CAM_wide, CAM_two, CAM_ots, CAM_close.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:]
mode, out, width, height, bvh_a, bvh_b = argv[0], argv[1], int(argv[2]), int(argv[3]), argv[4], argv[5]
PHI = math.radians(float(os.environ.get("PHI", "55")))
HANDSHAKE = int(os.environ.get("HS_FRAME", "130"))  # 24 fps frame where the hands meet
PLATE_CAM = Vector((0, -12, 0))  # plate camera ground position; facade ~34 m ahead (+Y)
MEET = Vector((0, 16, 0))
UP = Vector((0, 0, 1))

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 24, 1.0
scene.render.resolution_x, scene.render.resolution_y = width, height

RADIUS = {"Hips": 0.13, "Spine1": 0.13, "Spine2": 0.14, "Chest": 0.16, "Neck1": 0.05, "Neck2": 0.05,
          "Head": 0.10, "Shoulder": 0.05, "Arm": 0.05, "ForeArm": 0.04, "Hand": 0.035,
          "Leg": 0.075, "Shin": 0.055, "Foot": 0.045, "ToeBase": 0.035}


def material(name, rgb):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*rgb, 1)
    m.use_nodes = True
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*rgb, 1)
    return m


def load(bvh, name, rgb):
    bpy.ops.import_anim.bvh(filepath=bvh, axis_forward="-Z", axis_up="Y", global_scale=0.01,
                            use_fps_scale=True, update_scene_fps=False, update_scene_duration=False,
                            rotate_mode="NATIVE")
    rig = bpy.context.selected_objects[0]
    rig.name = name
    mat = material(f"Mannequin{name}", rgb)
    for b in rig.data.bones:
        key = b.name.replace("Left", "").replace("Right", "")
        if key not in RADIUS:
            continue
        if key == "Head":
            bpy.ops.mesh.primitive_uv_sphere_add(radius=0.11, segments=16, ring_count=8)
        else:
            bpy.ops.mesh.primitive_cylinder_add(radius=RADIUS[key], depth=b.length, vertices=12)
        m = bpy.context.object
        m.name = f"{name}_{b.name}"
        m.data.materials.append(mat)
        bpy.ops.object.shade_smooth()
        m.parent, m.parent_type, m.parent_bone = rig, "BONE", b.name
        m.rotation_euler = (math.radians(90), 0, 0)
        m.location = (0, -b.length / 2, 0)
    return rig


def bone(rig, name, f, tail=False):
    scene.frame_set(f)
    pb = rig.pose.bones[name]
    return rig.matrix_world @ (pb.tail if tail else pb.head)


def facing(rig, f):
    left = bone(rig, "LeftArm", f) - bone(rig, "RightArm", f)
    fwd = left.cross(UP)
    fwd.z = 0
    return fwd.normalized()


def camera(name, lens):
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    cam.data.lens = lens
    scene.collection.objects.link(cam)
    return cam


def keys(obj, path, pairs):
    for f, v in pairs:
        setattr(obj, path, v)
        obj.keyframe_insert(path, frame=f)


def aim(cam, targets, name):
    """TRACK_TO an empty that follows a weighted blend of bone heads [(rig, bone, influence), ...]."""
    t = bpy.data.objects.new(name, None)
    scene.collection.objects.link(t)
    acc = 0.0
    for rig, bname, w in targets:  # stacked constraints blend in sequence: w_i / (w_0 + .. + w_i) is a weighted mean
        acc += w
        c = t.constraints.new("COPY_LOCATION")
        c.target, c.subtarget, c.influence = rig, bname, w / acc
    tt = cam.constraints.new("TRACK_TO")
    tt.target, tt.track_axis, tt.up_axis = t, "TRACK_NEGATIVE_Z", "UP_Y"


def drop_islands(obj, min_verts):
    """Delete small disconnected pieces (floating shards of signs and posts left by the depth-edge cut)."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    seen, kill = set(), []
    for v in bm.verts:
        if v.index in seen:
            continue
        comp, stack = [], [v]
        seen.add(v.index)
        while stack:
            x = stack.pop()
            comp.append(x)
            for e in x.link_edges:
                y = e.other_vert(x)
                if y.index not in seen:
                    seen.add(y.index)
                    stack.append(y)
        if len(comp) < min_verts:
            kill.extend(comp)
    bmesh.ops.delete(bm, geom=kill, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    print("ISLANDS dropped", len(kill), "verts from", obj.name)


def settle_ground(plates):
    """Re-level the set in world space: fit the ground line z = a*y + b (10th-percentile z per 2 m depth bin near the
    centre line, foreground layer) and rotate/translate the plates so it lies on z = 0. The camera-space plane fit in
    moge_mesh.py only sees the nearest rows; this one spans the whole square."""
    import numpy as np
    fg = next(o for o in plates if o.name == "plate_fg")
    mw = np.array(fg.matrix_world)
    co = np.array([v.co[:] for v in fg.data.vertices])
    w = co @ mw[:3, :3].T + mw[:3, 3]
    ys, zs = [], []
    for y0 in np.arange(w[:, 1].min(), w[:, 1].max(), 2.0):
        m = (w[:, 1] >= y0) & (w[:, 1] < y0 + 2) & (np.abs(w[:, 0]) < 4)
        if m.sum() > 200:
            ys.append(y0 + 1)
            zs.append(np.percentile(w[m, 2], 10))
    a, b = np.polyfit(ys, zs, 1)
    fix = Matrix.Translation((0, 0, -b)) @ Matrix.Rotation(-math.atan(a), 4, "X")
    for o in plates:
        o.matrix_world = fix @ o.matrix_world
    print("SETTLE slope %.4f offset %.2f over %d bins" % (a, b, len(ys)))


def grey_ground(obj, mat, zmax):
    """Give set-mesh faces on the ground (all verts below zmax, world) a plain lit material that H3 re-renders,
    keeping the mesh's own ground shape so it still meets the facades."""
    obj.data.materials.append(mat)
    slot = len(obj.data.materials) - 1
    mw = obj.matrix_world
    zs = [(mw @ v.co).z for v in obj.data.vertices]
    n = 0
    for poly in obj.data.polygons:
        if max(zs[i] for i in poly.vertices) < zmax:
            poly.material_index = slot
            n += 1
    print("GROUND", obj.name, n, "faces")


def flat(angle_deg, length=1.0):
    a = math.radians(angle_deg)
    return Vector((math.cos(a), math.sin(a), 0)) * length


rig_a = load(bvh_a, "A", (0.62, 0.62, 0.62))
rig_b = load(bvh_b, "B", (0.45, 0.45, 0.47))
end = max(int(rig_a.animation_data.action.frame_range[1]), int(rig_b.animation_data.action.frame_range[1]))
scene.frame_start, scene.frame_end = 1, end

ha, hb = bone(rig_a, "Hips", HANDSHAKE), bone(rig_b, "Hips", HANDSHAKE)
fa, fb = facing(rig_a, HANDSHAKE), facing(rig_b, HANDSHAKE)
hand_gap = (bone(rig_a, "RightHand", HANDSHAKE) - bone(rig_b, "RightHand", HANDSHAKE)).length
gaps = [(bone(rig_a, "RightHand", f) - bone(rig_b, "RightHand", f)).length for f in range(1, end + 1)]
print("HANDGAP min %.2f at frame %d" % (min(gaps), 1 + gaps.index(min(gaps))))
print("STAGING", json.dumps({"a_hips": list(ha), "b_hips": list(hb), "a_facing": list(fa), "b_facing": list(fb),
                              "hand_gap": hand_gap, "end": end}))
pivot = (ha + hb) / 2
pivot.z = 0
xf = Matrix.Translation(MEET) @ Matrix.Rotation(PHI, 4, "Z") @ Matrix.Translation(-pivot)
for rig in (rig_a, rig_b):
    rig.matrix_world = xf @ rig.matrix_world
u = flat(math.degrees(PHI))  # A -> B


def dress(rig, blend):
    """Replace a mannequin with a textured SOMA-named rig (rig-test winner) baked from the BVH armature."""
    sys.path.insert(0, os.environ.get("RTLIB", "/root/p3/rt"))
    import rtlib
    with bpy.data.libraries.load(blend, link=False) as (src, dst):
        dst.objects = list(src.objects)
    arm = None
    for o in dst.objects:
        if o is not None and o.type in ("ARMATURE", "MESH"):
            scene.collection.objects.link(o)
            arm = o if o.type == "ARMATURE" else arm
    arm.name = f"{rig.name}_skin"
    ref = rtlib.import_bvh(rtlib.BLENDER_TPOSE, f"{rig.name}_tpose")
    ref.hide_render = True
    names = {b.name for b in rig.data.bones}
    mapping = {b.name: b.name for b in arm.data.bones if b.name in names}
    # scale=1: rtlib scales the whole world hips offset, which would pull a shorter rig toward the origin
    rtlib.retarget(rig, ref, arm, mapping, range(1, end + 1), root_bone="Hips", scale=1.0)
    for o in scene.objects:
        if o.parent is rig and o.type == "MESH":
            o.hide_render = True
    print("DRESSED", rig.name, len(mapping), "bones")


for rig, var in ((rig_a, "RIG_A"), (rig_b, "RIG_B")):
    if os.environ.get(var):
        dress(rig, os.environ[var])
A, B = xf @ Vector((ha.x, ha.y, 0)), xf @ Vector((hb.x, hb.y, 0))

if mode == "plate":
    obj_path, info = argv[6], json.load(open(argv[7]))
    bpy.ops.wm.obj_import(filepath=obj_path)
    base = os.path.abspath(os.path.splitext(obj_path)[0])
    for plate in list(bpy.context.selected_objects):
        png = f"{base}_{plate.name}.png" if os.path.exists(f"{base}_{plate.name}.png") else base + ".png"
        m = bpy.data.materials.new(f"Mat_{plate.name}")
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        tex = nt.nodes.new("ShaderNodeTexImage")
        emit = nt.nodes.new("ShaderNodeEmission")
        outn = nt.nodes.new("ShaderNodeOutputMaterial")
        tex.image = bpy.data.images.load(png)
        nt.links.new(tex.outputs["Color"], emit.inputs["Color"])
        nt.links.new(emit.outputs["Emission"], outn.inputs["Surface"])
        plate.data.materials.clear()
        plate.data.materials.append(m)
        if plate.name == "plate_fg":
            drop_islands(plate, int(os.environ.get("MIN_ISLAND", "3000")))
    settle_ground([o for o in scene.objects if o.name.startswith("plate_")])
    if os.environ.get("GREY_GROUND", "1") == "1":
        # The ground is where the plate breaks first (grazing angle, view-dependent wet reflections), and H3 copies
        # textured pixels: leave the ground grey so H3 re-renders it.
        gm = material("GroundGrey", (0.2, 0.2, 0.21))
        for plate in [o for o in scene.objects if o.name.startswith("plate_")]:
            grey_ground(plate, gm, float(os.environ.get("GROUND_CUT", "0.3")))
    w = bpy.data.worlds.new("World")
    w.use_nodes = True
    w.node_tree.nodes["Background"].inputs["Color"].default_value = (*info.get("mean_rgb", (0.05, 0.05, 0.07)), 1)
    scene.world = w
    scene.view_settings.view_transform = "Standard"  # show the plate texture as-is (AgX/Filmic dims it)
    for ang, col, e in ((-40, (1.0, 0.85, 0.7), 2.0), (140, (0.5, 0.7, 1.0), 1.0)):
        s = bpy.data.objects.new(f"Sun{ang}", bpy.data.lights.new(f"Sun{ang}", "SUN"))
        s.data.energy, s.data.color = e, col
        s.rotation_euler = (math.radians(50), 0, math.radians(ang))
        scene.collection.objects.link(s)
else:
    bpy.ops.mesh.primitive_plane_add(size=120, location=(0, 20, 0))
    bpy.context.object.data.materials.append(material("Ground", (0.3, 0.3, 0.31)))
    bpy.ops.mesh.primitive_cube_add(location=(0, 22 + 2, 7))
    bpy.context.object.scale = (25, 2, 7)
    bpy.context.object.data.materials.append(material("Facade", (0.5, 0.49, 0.47)))
    w = bpy.data.worlds.new("World")
    w.use_nodes = True
    w.node_tree.nodes["Background"].inputs["Color"].default_value = (0.1, 0.1, 0.12, 1)
    scene.world = w

# WIDE: from well back toward the facade, slow push while A walks in.
c = camera("CAM_wide", 32)
d = flat(85)
keys(c, "location", [(1, tuple(MEET - d * 13 + Vector((0, 0, 1.8)))), (107, tuple(MEET - d * 10.5 + Vector((0, 0, 1.7))))])
aim(c, [(rig_a, "Hips", 0.6), (rig_b, "Hips", 0.4)], "T_wide")
# TWO: 3/4 two-shot of the handshake, A's back three-quarters, B's front three-quarters.
c = camera("CAM_two", 40)
d = flat(100)
keys(c, "location", [(96, tuple(MEET - d * 3.8 + Vector((0, 0, 1.5)))), (168, tuple(MEET - d * 3.4 + Vector((0, 0, 1.5))))])
aim(c, [(rig_a, "Spine2", 0.5), (rig_b, "Spine2", 0.5)], "T_two")
# OTS: over A's right shoulder onto B.
c = camera("CAM_ots", 50)
right_a = u.cross(UP).normalized()  # A faces u; right = u x up
p = A - u * 1.7 + right_a * 1.1
keys(c, "location", [(132, tuple(p + Vector((0, 0, 1.62)))), (216, tuple(p + u * 0.15 + Vector((0, 0, 1.62))))])
aim(c, [(rig_b, "Head", 0.8), (rig_a, "Head", 0.2)], "T_ots")
# CLOSE: B, medium close-up, slightly off A's eyeline.
c = camera("CAM_close", 70)
d = flat(math.degrees(PHI) + 35)
keys(c, "location", [(144, tuple(B - d * 1.9 + Vector((0, 0, 1.55)))), (216, tuple(B - d * 1.75 + Vector((0, 0, 1.55))))])
aim(c, [(rig_b, "Head", 1.0)], "T_close")
scene.camera = bpy.data.objects["CAM_wide"]
scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath=out)
print("BUILD_OK", mode)
