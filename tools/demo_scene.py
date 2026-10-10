"""Build a small demo project in the open Blender file (add-on must be enabled).

    blender --python tools/demo_scene.py -- [project_root]

Creates a stick-figure rig, a rooftop with a low wall, three shots with
action prompts, and Start + Path targets on the first shot. Point the add-on
at tools/mock_orchestrator.py to run the pipeline on it.
"""

import os
import sys
import tempfile

import bpy

BONES = [  # name, head, tail, parent
    ("Hips", (0, 0, 0.95), (0, 0, 1.05), None),
    ("Spine", (0, 0, 1.05), (0, 0, 1.3), "Hips"),
    ("Spine1", (0, 0, 1.3), (0, 0, 1.45), "Spine"),
    ("Neck", (0, 0, 1.45), (0, 0, 1.55), "Spine1"),
    ("Head", (0, 0, 1.55), (0, 0, 1.75), "Neck"),
    ("LeftArm", (0.18, 0, 1.42), (0.45, 0, 1.42), "Spine1"),
    ("LeftForeArm", (0.45, 0, 1.42), (0.7, 0, 1.42), "LeftArm"),
    ("LeftHand", (0.7, 0, 1.42), (0.8, 0, 1.42), "LeftForeArm"),
    ("RightArm", (-0.18, 0, 1.42), (-0.45, 0, 1.42), "Spine1"),
    ("RightForeArm", (-0.45, 0, 1.42), (-0.7, 0, 1.42), "RightArm"),
    ("RightHand", (-0.7, 0, 1.42), (-0.8, 0, 1.42), "RightForeArm"),
    ("LeftUpLeg", (0.1, 0, 0.95), (0.1, 0, 0.5), "Hips"),
    ("LeftLeg", (0.1, 0, 0.5), (0.1, 0, 0.08), "LeftUpLeg"),
    ("LeftFoot", (0.1, 0, 0.08), (0.1, 0.15, 0.0), "LeftLeg"),
    ("RightUpLeg", (-0.1, 0, 0.95), (-0.1, 0, 0.5), "Hips"),
    ("RightLeg", (-0.1, 0, 0.5), (-0.1, 0, 0.08), "RightUpLeg"),
    ("RightFoot", (-0.1, 0, 0.08), (-0.1, 0.15, 0.0), "RightLeg"),
]

SHOTS = [
    ("a man walks briskly toward a low wall, vaults over it and lands in a crouch", 4.0),
    ("he stands up slowly and looks around, catching his breath", 3.0),
    ("he sprints to the edge of the rooftop and stops abruptly", 3.5),
]


def make_rig():
    arm = bpy.data.armatures.new("HeroRig")
    arm.display_type = "STICK"
    rig = bpy.data.objects.new("HeroRig", arm)
    rig.show_in_front = True
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for name, head, tail, parent in BONES:
        b = arm.edit_bones.new(name)
        b.head, b.tail = head, tail
        if parent:
            b.parent = arm.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def make_set():
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (0.8, 0.2, 0.6)
    bpy.context.scene.collection.objects.link(sun)
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
    bpy.context.object.name = "Rooftop"
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 3.0, 0.4))
    wall = bpy.context.object
    wall.name = "LowWall"
    wall.scale = (3.0, 0.3, 0.8)


def build(root=None):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    scene = bpy.context.scene
    p = scene.chimera
    p.project_name = "demo"
    p.root = root or tempfile.mkdtemp(prefix="chimera_demo_")
    p.look_prompt = "overcast concrete rooftop, cinematic, 35mm film grain"
    brief = bpy.data.texts.get("demo_brief") or bpy.data.texts.new("demo_brief")
    brief.clear()
    brief.write("A parkour runner crosses a rooftop at dusk. He vaults a low wall, "
                "recovers, then sprints to the edge and stops.\n")
    p.brief = brief
    make_set()
    rig = make_rig()
    p.shots.clear()
    for prompt, dur in SHOTS:
        bpy.ops.chimera.shot_add()
        shot = p.active_shot
        shot.action_prompt = prompt
        shot.duration_s = dur
        shot.characters[0].rig_object = rig
    bpy.ops.chimera.layout()
    # Cameras: a 3/4 view for each shot.
    for i, shot in enumerate(p.shots):
        cam = bpy.data.objects[f"CAM_{shot.name}"]
        cam.location = (6.0 - 2 * i, -5.0 + 2 * i, 1.7)
        cam.rotation_euler = (1.4, 0.0, 0.85 - 0.3 * i)
    # Targets on shot 1: start behind the wall, path over it.
    p.active_shot_index = 0
    scene.cursor.location = (0, -2.0, 0)
    bpy.ops.chimera.add_start()
    bpy.ops.chimera.add_path()
    path = bpy.context.object
    path.data.splines[0].bezier_points[1].co = (0.5, 6.5, 0)
    scene.cursor.location = (0.4, 5.5, 0)
    scene.frame_set(1 + 84)
    bpy.ops.chimera.add_waypoint()
    scene.frame_set(1)
    scene.cursor.location = (0, 0, 0)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    return p


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    build(os.path.abspath(argv[0]) if argv else None)
