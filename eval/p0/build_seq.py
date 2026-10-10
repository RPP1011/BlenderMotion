"""Shot builder for the multi-shot test sequence: Kimodo BVH clips -> staged grey mannequins.

blender -b --python build_seq.py -- <shot> <out.blend> <frames> <width> <height> <a.bvh> [b.bvh]

shot:
  turn  studio turntable: A stands, camera orbits 360 degrees (character-sheet source)
  s1    dusk rooftop: A stands at the parapet, turns and walks away from it
  s2    corridor: A walks toward a camera that tracks backwards ahead of him
  s3    night street: A walks up and stops; B waits where he stops, turned to face him
  s4    same street: A and B face each other and talk, over-the-shoulder camera

Armatures are named "A" and "B", the camera "CAM_p0" (what blender_worker/passes.py expects).
Kimodo is Y-up, +Z forward, BVH in cm (imported at BVH_SCALE=0.01).
"""
import math
import os
import random
import sys

import bpy
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:]
shot, out = argv[0], argv[1]
frames, width, height = int(argv[2]), int(argv[3]), int(argv[4])
bvhs = argv[5:]
GAP = 1.4
UP = Vector((0, 0, 1))

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 24, 1.0
scene.frame_start, scene.frame_end = 1, frames
scene.render.resolution_x, scene.render.resolution_y = width, height
rng = random.Random(3)


def material(name, rgb, rough=0.6):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*rgb, 1)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1)
    bsdf.inputs["Roughness"].default_value = rough
    return m


MAT = {k: material(k, v) for k, v in {
    "dark": (0.12, 0.12, 0.13), "pave": (0.35, 0.35, 0.36), "wall": (0.42, 0.4, 0.38),
    "prop": (0.5, 0.48, 0.45), "light": (0.85, 0.85, 0.8)}.items()}


def box(name, loc, size, mat="prop", rot=0.0):
    bpy.ops.mesh.primitive_cube_add(location=loc, rotation=(0, 0, rot))
    o = bpy.context.object
    o.name = name
    o.scale = (size[0] / 2, size[1] / 2, size[2] / 2)
    o.data.materials.append(MAT[mat])
    return o


def cyl(loc, r, depth, mat="prop"):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=depth, location=loc)
    bpy.context.object.data.materials.append(MAT[mat])


RADIUS = {"Hips": 0.13, "Spine1": 0.13, "Spine2": 0.14, "Chest": 0.16, "Neck1": 0.05, "Neck2": 0.05,
          "Head": 0.10, "Shoulder": 0.05, "Arm": 0.05, "ForeArm": 0.04, "Hand": 0.035,
          "Leg": 0.075, "Shin": 0.055, "Foot": 0.045, "ToeBase": 0.035}


def load(bvh, name, rgb):
    bpy.ops.import_anim.bvh(filepath=bvh, axis_forward="-Z", axis_up="Y",
                            global_scale=float(os.environ.get("BVH_SCALE", "0.01")),
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


def pose_at(rig, f):
    """World hips position (z=0) and horizontal facing at frame f (facing f, up z: left = z x f)."""
    scene.frame_set(f)
    pb = rig.pose.bones
    hips = rig.matrix_world @ pb["Hips"].head
    left = rig.matrix_world @ pb["LeftArm"].head - rig.matrix_world @ pb["RightArm"].head
    fwd = left.cross(UP)
    fwd.z = hips.z = 0
    return hips, fwd.normalized()


def yaw(v):
    return math.atan2(v.x, v.y)  # 0 = +Y, positive toward +X


def turn(rig, pivot, angle):
    rig.matrix_world = (Matrix.Translation(pivot) @ Matrix.Rotation(angle, 4, "Z")
                        @ Matrix.Translation(-pivot) @ rig.matrix_world)


def face(rig, f, direction, at=None):
    """Rotate the clip so the rig faces `direction` at frame f; optionally move its hips there to `at`."""
    p, fwd = pose_at(rig, f)
    turn(rig, p, yaw(fwd) - yaw(direction))
    if at is not None:
        p, _ = pose_at(rig, f)
        rig.location += at - p


def travel_along_y(rig):
    """Rotate/translate the clip so its start->end travel runs along +Y from the origin."""
    p0, _ = pose_at(rig, 1)
    p1, _ = pose_at(rig, frames)
    turn(rig, p0, yaw(p1 - p0))
    rig.location -= p0


def world(rgb, strength=1.0):
    w = bpy.data.worlds.new("World")
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (*rgb, 1)
    bg.inputs["Strength"].default_value = strength
    scene.world = w
    sun = bpy.data.objects.new("Key", bpy.data.lights.new("Key", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(55), 0, math.radians(40))
    scene.collection.objects.link(sun)


def camera(lens=35):
    cam = bpy.data.objects.new("CAM_p0", bpy.data.cameras.new("CAM_p0"))
    cam.data.lens = lens
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def aim(cam, targets, z=None):
    """Track an empty that follows the hips of `targets` [(rig, influence), ...] (stacked blends)."""
    t = bpy.data.objects.new("CAM_target", None)
    scene.collection.objects.link(t)
    for rig, infl in targets:
        c = t.constraints.new("COPY_LOCATION")
        c.target, c.subtarget, c.influence = rig, "Hips", infl
        if z is not None:
            c.use_z = False
    if z is not None:
        t.location.z = z
    tt = cam.constraints.new("TRACK_TO")
    tt.target, tt.track_axis, tt.up_axis = t, "TRACK_NEGATIVE_Z", "UP_Y"


def keys(obj, path, pairs, index=-1):
    for f, v in pairs:
        if index >= 0:
            getattr(obj, path)[index] = v
        else:
            setattr(obj, path, v)
        obj.keyframe_insert(path, index=index, frame=f)


def street(c):
    """Street along Y around c: road, kerbs, building rows with window bands, lamps, crates, a car."""
    box("Road", (c.x, c.y, -0.05), (60, 80, 0.1), "dark")
    for side in (-1, 1):
        x0 = c.x + side * 6.5
        box(f"Kerb{side}", (x0, c.y, 0.08), (3, 80, 0.16), "pave")
        y = c.y - 40
        while y < c.y + 40:
            w, h = rng.uniform(5, 9), rng.uniform(7, 18)
            box(f"Bldg{side}_{int(y)}", (x0 + side * 5.5, y + w / 2, h / 2), (8, w - 0.3, h), "wall")
            for k in range(1, int(h // 3)):
                box(f"Win{side}_{int(y)}_{k}", (x0 + side * 1.45, y + w / 2, k * 3.0), (0.2, w - 1.2, 1.2), "dark")
            y += w
        for j in range(-4, 5):
            lx, ly = x0 - side * 1.1, c.y + j * 8
            cyl((lx, ly, 2.25), 0.07, 4.5)
            bpy.ops.mesh.primitive_uv_sphere_add(radius=0.25, location=(lx, ly, 4.6))
            bpy.context.object.data.materials.append(MAT["light"])
    for i in range(6):
        s = rng.uniform(0.5, 0.9)
        box(f"Crate{i}", (c.x + rng.choice((-1, 1)) * rng.uniform(4.2, 5.2), c.y + rng.uniform(-8, 8), s / 2),
            (s, s, s))
    box("CarBody", (c.x - 3.8, c.y + 6, 0.7), (1.9, 4.4, 0.9))
    box("CarCabin", (c.x - 3.8, c.y + 5.8, 1.45), (1.7, 2.4, 0.6))
    world((0.08, 0.09, 0.13))


rig_a = load(bvhs[0], "A", (0.62, 0.62, 0.62))
rig_b = load(bvhs[1], "B", (0.45, 0.45, 0.47)) if len(bvhs) > 1 else None

if shot == "turn":
    face(rig_a, 1, Vector((0, -1, 0)), at=Vector((0, 0, 0)))
    bpy.ops.mesh.primitive_plane_add(size=30)
    bpy.context.object.data.materials.append(MAT["pave"])
    world((0.5, 0.5, 0.52))
    cam = camera(35)
    pivot = bpy.data.objects.new("CAM_pivot", None)
    scene.collection.objects.link(pivot)
    cam.parent = pivot
    cam.location = (0, -4.2, 1.2)
    keys(pivot, "rotation_euler", [(1, 0.0), (frames, math.radians(360))], index=2)
    for fc in pivot.animation_data.action.fcurves if hasattr(pivot.animation_data.action, "fcurves") else []:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    aim(cam, [(rig_a, 1.0)], z=0.95)

elif shot == "s1":
    travel_along_y(rig_a)
    a0, f0 = pose_at(rig_a, 1)
    a1, _ = pose_at(rig_a, frames)
    side = f0.cross(UP).normalized()
    par = a0 + f0 * 0.9
    box("Roof", (par.x - f0.x * 15, par.y - f0.y * 15, -0.1), (40, 40, 0.2), "pave", rot=-yaw(f0))
    box("Parapet", (par.x, par.y, 0.55), (30, 0.3, 1.1), "wall", rot=-yaw(f0))
    for i in range(40):  # skyline beyond the parapet
        d, lat, h = rng.uniform(60, 220), rng.uniform(-160, 160), rng.uniform(20, 90)
        p = par + f0 * d + side * lat
        box(f"Tower{i}", (p.x, p.y, -60 + (60 + h) / 2), (rng.uniform(10, 25), rng.uniform(10, 25), 60 + h), "wall")
    st = a1 - side * 6.0 + (a1 - a0).normalized() * 2.0
    box("Stairwell", (st.x, st.y, 1.5), (3, 3, 3), "wall", rot=-yaw(f0))
    cyl((a0.x + side.x * 5, a0.y + side.y * 5 + 4, 2.6), 1.2, 2.2)  # water tank
    for k in range(4):
        cyl((a0.x + side.x * 5 + (k % 2 - 0.5) * 1.6, a0.y + side.y * 5 + 4 + (k // 2 - 0.5) * 1.6, 0.75), 0.08, 1.5)
    for i in range(3):
        q = a0 - side * (4 + i * 1.6) + (a1 - a0).normalized() * 3
        box(f"AC{i}", (q.x, q.y, 0.5), (1.2, 1.2, 1.0))
    world((0.55, 0.42, 0.38))
    cam = camera(35)
    # Behind him at the start (he is silhouetted against the skyline), so he turns and walks toward us.
    keys(cam, "location", [(1, tuple(a0 - f0 * 6.0 + side * 2.4 + Vector((0, 0, 1.7)))),
                           (frames, tuple(a0 - f0 * 6.6 + side * 2.0 + Vector((0, 0, 1.7))))])
    aim(cam, [(rig_a, 1.0)], z=1.1)

elif shot == "s2":
    travel_along_y(rig_a)
    a1, _ = pose_at(rig_a, frames)
    length = a1.y + 12
    box("Floor", (0, length / 2 - 4, -0.05), (2.8, length + 8, 0.1), "pave")
    box("Ceiling", (0, length / 2 - 4, 2.85), (2.8, length + 8, 0.1), "wall")
    for sx in (-1, 1):
        box(f"Wall{sx}", (sx * 1.45, length / 2 - 4, 1.4), (0.1, length + 8, 2.9), "wall")
        y = -3 + (sx + 1) * 0.9
        while y < length:
            box(f"Door{sx}_{int(y * 10)}", (sx * 1.38, y, 1.05), (0.06, 0.95, 2.1), "dark")
            y += 3.6
    y = -2.0
    while y < length:
        box(f"Lamp{int(y * 10)}", (0, y, 2.78), (0.4, 1.2, 0.05), "light")
        y += 3.0
    box("EndWall", (0, length + 0.05, 1.4), (2.8, 0.1, 2.9), "wall")
    box("EndWindow", (0, length, 1.5), (1.2, 0.12, 1.2), "light")
    world((0.2, 0.22, 0.2))
    cam = camera(32)
    c = cam.constraints.new("COPY_LOCATION")  # rides ahead of the hips along Y
    c.target, c.subtarget = rig_a, "Hips"
    c.use_x = c.use_z = False
    c.use_offset = True
    cam.location = (0.35, 3.8, 1.6)
    aim(cam, [(rig_a, 1.0)], z=1.25)

elif shot == "s3":
    travel_along_y(rig_a)
    a1, a_end = pose_at(rig_a, frames)
    face(rig_b, frames, -a_end, at=a1 + a_end * GAP)
    b1, _ = pose_at(rig_b, frames)
    meet = (a1 + b1) / 2
    street(meet)
    cam = camera(35)
    pivot = bpy.data.objects.new("CAM_pivot", None)
    scene.collection.objects.link(pivot)
    pivot.location = (meet.x, meet.y - 1.0, 0)
    cam.parent = pivot
    keys(pivot, "rotation_euler", [(1, math.radians(-20)), (frames, math.radians(5))], index=2)
    keys(cam, "location", [(1, (7.5, 0, 1.55)), (frames, (5.5, 0, 1.55))])
    aim(cam, [(rig_a, 1.0), (rig_b, 0.5)], z=1.0)

elif shot == "s4":
    mid = frames // 2
    face(rig_a, mid, Vector((1, 0, 0)), at=Vector((0, 0, 0)))
    face(rig_b, mid, Vector((-1, 0, 0)), at=Vector((GAP, 0, 0)))
    street(Vector((GAP / 2, 0, 0)))
    cam = camera(40)
    keys(cam, "location", [(1, (-1.9, -1.5, 1.6)), (frames, (-1.5, -1.25, 1.6))])
    aim(cam, [(rig_a, 1.0), (rig_b, 0.75)], z=1.35)
else:
    raise SystemExit(f"unknown shot {shot}")

scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath=out)
print("BUILD_OK", shot)
