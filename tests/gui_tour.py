"""Record the tutorial clips: drive the real UI step by step and screen-record each step.

Run via tests/gui_tour.sh (Xvfb + ffmpeg x11grab, orchestrator with fake models and the
real Blender workers). Each step writes <out>/<name>.mp4 and a still <name>.png; captions
go to <out>/steps.json for tests/gui_tour_post.sh.
"""

import json
import os
import subprocess
import sys
import time

import addon_utils
import bpy
from mathutils import Euler

addon_utils.enable("chimera_director", default_set=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import demo_scene  # noqa: E402

OUT = os.environ.get("CAPTURE_DIR", "/tmp/chimera_tour")
URL = os.environ.get("CHIMERA_E2E_URL", "")
SIZE = os.environ.get("SCREEN", "1920x1080")
os.makedirs(OUT, exist_ok=True)
STEPS = []

BRIEF = ("A parkour runner walks across a rooftop toward a low wall. "
         "He vaults over the wall and lands in a crouch. "
         "He stands up slowly and looks around.")


def _open_panels():
    """Panels that start closed are re-registered open so the clips can show them."""
    from chimera_director.ui import panel_shot
    for cls in (panel_shot.CHIMERA_PT_shot_targets, panel_shot.CHIMERA_PT_shot_status):
        bpy.utils.unregister_class(cls)
        cls.bl_options = set()
        bpy.utils.register_class(cls)


class Rec:
    def __init__(self, name, caption):
        self.name, self.caption = name, caption

    def __enter__(self):
        path = os.path.join(OUT, self.name + ".mp4")
        self.proc = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-draw_mouse", "0", "-framerate", "15",
             "-video_size", SIZE, "-i", os.environ["DISPLAY"], "-c:v", "libx264", "-preset", "ultrafast",
             "-crf", "16", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
        STEPS.append({"name": self.name, "caption": self.caption})
        print("REC", self.name, flush=True)
        return self

    def snap(self):
        subprocess.run(["import", "-window", "root", os.path.join(OUT, self.name + ".png")], check=False)
        self.snapped = True

    def __exit__(self, *exc):
        if not getattr(self, "snapped", False):
            self.snap()
        self.proc.communicate(b"q", timeout=30)
        with open(os.path.join(OUT, "steps.json"), "w") as f:
            json.dump(STEPS, f, indent=1)


def win():
    return bpy.context.window_manager.windows[0]


def area(kind, sidebar=None):
    for a in win().screen.areas:
        if a.type == kind and (sidebar is None or a.spaces.active.show_region_ui == sidebar):
            return a
    return None


def ctx(kind="VIEW_3D", sidebar=True, region="WINDOW"):
    a = area(kind, sidebar)
    return dict(window=win(), screen=win().screen, area=a, region=next(r for r in a.regions if r.type == region))


def scroll_sidebar(lines):
    """Scroll the Director sidebar (positive = down)."""
    with bpy.context.temp_override(**ctx(region="UI")):
        for _ in range(abs(lines)):
            (bpy.ops.view2d.scroll_down if lines > 0 else bpy.ops.view2d.scroll_up)(deltay=1)


def pause(seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        yield 0.1


def wait_stage(shot, key, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = shot.stages.get(key)
        if st and st.status in ("done", "failed", "cancelled"):
            return
        yield 0.3


def play(seconds):
    with bpy.context.temp_override(**ctx(sidebar=False)):
        bpy.ops.screen.animation_play()
    yield from pause(seconds)
    with bpy.context.temp_override(**ctx(sidebar=False)):
        bpy.ops.screen.animation_cancel(restore_frame=False)


def script():
    from chimera_director import prefs
    yield 1.0
    _open_panels()
    if URL:
        prefs.get().server_url = URL
    # Start from a scene with a set, a rig and a brief, but no shots.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    scene = bpy.context.scene
    p = scene.chimera
    p.project_name = "demo"
    p.root = os.path.join(OUT, "project")
    p.look_prompt = "overcast concrete rooftop, cinematic, 35mm film grain"
    p.brief = bpy.data.texts.new("brief")
    p.brief.write(BRIEF + "\n")
    demo_scene.make_set()
    rig = demo_scene.make_rig()
    for o in bpy.context.selected_objects:
        o.select_set(False)
    yield 1.0

    with Rec("01_workspace", "Director Workspace: sidebar, shot camera, NLA and timeline"):
        yield from pause(1.5)
        bpy.ops.chimera.setup_workspace()
        for _ in range(60):
            yield 0.2
            if area("VIEW_3D", True) and area("NLA_EDITOR"):
                break
        yield from pause(1.0)
        r3d = area("VIEW_3D", True).spaces.active.region_3d
        r3d.view_location = (0.0, 2.0, 0.8)
        r3d.view_distance = 14.0
        r3d.view_rotation = Euler((1.15, 0.0, 0.75)).to_quaternion()
        yield from pause(2.0)

    with Rec("02_plan", "Plan Shots from Brief: the planner splits the brief into shots"):
        yield from pause(1.5)
        bpy.ops.chimera.plan_from_brief()
        for i, s in enumerate(p.shots):  # a 3/4 view on each shot
            cam = bpy.data.objects[f"CAM_{s.name}"]
            cam.location = (5.5 - 1.5 * i, -3.5 + 3 * i, 1.6)
            cam.rotation_euler = (1.45, 0.0, 0.9 - 0.2 * i)
        yield from pause(1.0)
        for i in range(len(p.shots)):
            p.active_shot_index = i
            yield from pause(1.0)
        p.active_shot_index = 0
        yield from pause(1.5)

    shot = p.active_shot
    with Rec("03_character", "Characters: add one per performer and point Rig at its armature"):
        yield from pause(1.5)
        if not shot.characters:
            bpy.ops.chimera.character_add()
            yield from pause(1.0)
        shot.characters[0].char_id = "hero"
        yield from pause(0.8)
        shot.characters[0].rig_object = rig
        yield from pause(2.5)

    with Rec("04_prompt", "Action prompt: the pencil opens a wide editor") as r:
        yield from pause(1.0)
        with bpy.context.temp_override(**ctx()):
            bpy.ops.chimera.edit_prompt("INVOKE_DEFAULT")
        yield from pause(2.0)
        r.snap()
        yield from pause(1.0)
        subprocess.run(["xdotool", "key", "Escape"], check=False)
        yield from pause(1.0)

    with Rec("05_targets", "Targets: Start (position + facing), then Path and Waypoints at the 3D cursor"):
        scroll_sidebar(12)
        yield from pause(1.5)
        scene.cursor.location = (0, -2.0, 0)
        bpy.ops.chimera.add_start()
        yield from pause(1.5)
        bpy.ops.chimera.add_path()
        path = bpy.context.object
        yield from pause(1.0)
        for k in range(11):  # drag the path end over the wall
            path.data.splines[0].bezier_points[1].co = (0.05 * k, 4.0 + 0.25 * k, 0)
            path.update_tag()
            yield 0.08
        yield from pause(1.0)
        rng = (scene.timeline_markers[0].frame, 0)
        scene.frame_set(rng[0] + 84)
        scene.cursor.location = (0.4, 5.5, 0)
        bpy.ops.chimera.add_waypoint()
        yield from pause(2.5)
        scene.frame_set(rng[0])
        scroll_sidebar(-12)

    with Rec("06_motion", "Generate Motion: takes arrive as variants v1..vN on the shot's NLA track") as r:
        yield from pause(1.0)
        bpy.ops.chimera.generate_motion()
        yield from wait_stage(shot, "motion")
        yield from pause(1.5)
        yield from play(4.5)
        r.snap()
        yield from pause(1.0)

    with Rec("07_passes_video", "Render Passes, then Generate Video; the eval scores pose accuracy (PCK)") as r:
        yield from pause(1.0)
        bpy.ops.chimera.render_passes()
        yield from wait_stage(shot, "passes")
        yield from pause(1.5)
        bpy.ops.chimera.generate_video()
        yield from wait_stage(shot, "eval")
        yield from pause(1.0)
        scroll_sidebar(25)
        yield from pause(2.0)
        r.snap()
        yield from pause(1.0)
        scroll_sidebar(-25)

    with Rec("08_stale", "Edit anything upstream and the affected stages turn stale; nothing re-runs by itself"):
        yield from pause(1.5)
        start = next(o for o in bpy.data.objects if o.name.startswith("START_"))
        for k in range(10):
            start.location.x = -0.08 * k
            yield 0.1
        bpy.ops.chimera.refresh_hash()
        yield from pause(3.0)

    with Rec("09_follow", "Follow Playhead: scrubbing into a shot makes it the active shot"):
        yield from pause(1.0)
        last = scene.frame_end
        for f in range(scene.frame_start, last, 3):
            scene.frame_set(f)
            yield 0.05
        yield from pause(1.5)
        scene.frame_set(scene.frame_start)
        yield from pause(1.0)

    with Rec("10_assemble", "Assemble Edit: generated clips are laid out in the edit scene"):
        yield from pause(1.0)
        bpy.ops.chimera.assemble_vse()
        yield from pause(1.0)
        with bpy.context.temp_override(**ctx()):
            bpy.ops.chimera.open_edit()
        yield from pause(0.5)
        seq_area = area("NLA_EDITOR")
        seq_area.ui_type = "SEQUENCE_EDITOR"
        cam_area = area("VIEW_3D", False)
        cam_area.ui_type = "SEQUENCE_EDITOR"
        cam_area.spaces.active.view_type = "PREVIEW"
        yield from pause(1.0)
        with bpy.context.temp_override(window=win(), screen=win().screen, area=seq_area,
                                       region=next(r for r in seq_area.regions if r.type == "WINDOW")):
            bpy.ops.sequencer.view_all()
        bpy.context.scene.frame_set(40)
        yield from pause(3.0)


def run():
    gen = script()

    def step():
        try:
            return next(gen)
        except StopIteration:
            bpy.ops.wm.quit_blender()
            return None
        except Exception:
            import traceback
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
            return None

    bpy.app.timers.register(step, first_interval=1.0)


run()
