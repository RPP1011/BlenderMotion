"""P0 shot builder: Kimodo BVH -> grey capsule mannequin on a rooftop, tracked camera.

blender -b --python build_shot.py -- <motion.bvh> <out.blend> [frames]
Saves a .blend whose scene matches what blender_worker/passes.py expects:
camera CAM_p0, armature "Kimodo", frames 1..frames.
"""
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
bvh, out = argv[0], argv[1]
frames = int(argv[2]) if len(argv) > 2 else 124

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 24, 1.0
scene.frame_start, scene.frame_end = 1, frames

# Kimodo is Y-up, +Z forward; Blender's importer maps that into Z-up.
bpy.ops.import_anim.bvh(filepath=bvh, axis_forward="-Z", axis_up="Y",
                        global_scale=float(os.environ.get("BVH_SCALE", "0.01")),
                        use_fps_scale=True, update_scene_fps=False, update_scene_duration=False,
                        rotate_mode="NATIVE")
rig = bpy.context.selected_objects[0]
rig.name = "Kimodo"
src_end = int(rig.animation_data.action.frame_range[1])
print("BVH_FRAMES", src_end, "SCENE_FPS", scene.render.fps)

# Grey capsule mannequin: one cylinder per body bone, bone-parented.
grey = bpy.data.materials.new("Mannequin")
grey.diffuse_color = (0.55, 0.55, 0.55, 1)
grey.use_nodes = True
grey.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.55, 0.55, 0.55, 1)
grey.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.6
RADIUS = {"Hips": 0.13, "Spine1": 0.13, "Spine2": 0.14, "Chest": 0.16, "Neck1": 0.05, "Neck2": 0.05,
          "Head": 0.10, "Shoulder": 0.05, "Arm": 0.05, "ForeArm": 0.04, "Hand": 0.035,
          "Leg": 0.075, "Shin": 0.055, "Foot": 0.045, "ToeBase": 0.035}
for b in rig.data.bones:
    key = b.name.replace("Left", "").replace("Right", "")
    if key not in RADIUS:
        continue
    r, length = RADIUS[key], b.length
    if key == "Head":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.11, segments=16, ring_count=8)
    else:
        bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=length, vertices=12)
    m = bpy.context.object
    m.data.materials.append(grey)
    bpy.ops.object.shade_smooth()
    m.parent = rig
    m.parent_type = "BONE"
    m.parent_bone = b.name
    # Bone-parented children sit at the bone's tail, Y along the bone.
    m.rotation_euler = (math.radians(90), 0, 0)
    m.location = (0, -length / 2 if key != "Head" else -length / 2, 0)

# Set: concrete roof, low wall, sun + sky.
bpy.ops.mesh.primitive_plane_add(size=40)
roof = bpy.context.object
mat = bpy.data.materials.new("Concrete")
mat.use_nodes = True
mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.3, 0.3, 0.32, 1)
roof.data.materials.append(mat)
sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
sun.data.energy = 3.5
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun)
world = bpy.data.worlds.new("Sky")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.45, 0.5, 0.6, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.8
scene.world = world

# Camera: 3/4 view from the side, following the hips' ground position.
hips = rig.pose.bones["Hips"]
track = bpy.data.objects.new("CAM_target", None)
scene.collection.objects.link(track)
c = track.constraints.new("COPY_LOCATION")
c.target, c.subtarget = rig, "Hips"
cam = bpy.data.objects.new("CAM_p0", bpy.data.cameras.new("CAM_p0"))
cam.data.lens = 35
scene.collection.objects.link(cam)
scene.frame_set(1)
p0 = rig.matrix_world @ hips.head
scene.frame_set(min(frames, src_end))
p1 = rig.matrix_world @ hips.head
mid = (p0 + p1) / 2
travel = (p1 - p0)
travel.z = 0
side = Vector((-travel.y, travel.x, 0)).normalized() if travel.length > 0.2 else Vector((1, 0, 0))
dist = max(4.5, travel.length * 1.1 + 2.5)
cam.location = mid + side * dist + Vector((0, 0, 0.6)) - travel.normalized() * 1.0 if travel.length > 0.2 \
    else mid + side * dist + Vector((0, 0, 0.6))
cam.location.z = 1.4
tt = cam.constraints.new("TRACK_TO")
tt.target, tt.track_axis, tt.up_axis = track, "TRACK_NEGATIVE_Z", "UP_Y"
scene.camera = cam
scene.render.resolution_x, scene.render.resolution_y = 960, 544
scene.frame_set(1)
print("CAM", tuple(round(v, 2) for v in cam.location), "TRAVEL", round(travel.length, 2))
bpy.ops.wm.save_as_mainfile(filepath=out)
print("BUILD_OK")
