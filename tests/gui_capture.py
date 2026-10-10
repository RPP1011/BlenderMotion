"""Drive the real Blender UI through the pipeline and screenshot each state.

Run via tests/gui_capture.sh (needs Xvfb + ImageMagick `import`). Not part of
the headless suite; it exists so UI changes can be looked at, not guessed.
"""

import os
import subprocess
import sys
import time

import addon_utils
import bpy

addon_utils.enable("chimera_director", default_set=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import demo_scene  # noqa: E402

OUT = os.environ.get("CAPTURE_DIR", "/tmp/chimera_capture")
URL = os.environ.get("CHIMERA_E2E_URL", "")
os.makedirs(OUT, exist_ok=True)


def capture(name):
    for _ in range(3):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    path = os.path.join(OUT, name + ".png")
    subprocess.run(["import", "-window", "root", path], check=False)
    print("CAPTURED", path, flush=True)


def win_ctx():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D" and a.spaces.active.show_region_ui)
    return dict(window=win, screen=win.screen, area=area,
                region=next(r for r in area.regions if r.type == "WINDOW"))


def wait_stage(shot, key, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = shot.stages.get(key)
        if st and st.status in ("done", "failed", "cancelled"):
            return st.status
        yield 0.5


def script():
    from chimera_director import prefs
    yield 1.0
    p = demo_scene.build(os.path.join(OUT, "project"))
    if URL:
        prefs.get().server_url = URL
    bpy.ops.chimera.setup_workspace()
    for _ in range(50):  # the layout is built over several redraws
        yield 0.2
        win = bpy.context.window_manager.windows[0]
        if any(a.type == "VIEW_3D" and a.spaces.active.show_region_ui for a in win.screen.areas) \
                and any(a.type == "NLA_EDITOR" for a in win.screen.areas):
            break
    yield 0.5
    ctx = win_ctx()
    with bpy.context.temp_override(**ctx):
        bpy.ops.view3d.view_all()
    p.active_shot_index = 0
    yield 1.0
    capture("01_fresh")

    with bpy.context.temp_override(**win_ctx()):
        bpy.ops.chimera.edit_prompt("INVOKE_DEFAULT")
    yield 1.0
    capture("02_prompt_dialog")
    # dismiss the dialog
    subprocess.run(["xdotool", "key", "Escape"], check=False)
    yield 0.5

    if not URL:
        return
    shot = p.active_shot
    bpy.ops.chimera.generate_motion()
    yield 1.5
    capture("03_motion_running")
    yield from wait_stage(shot, "motion")
    yield 1.0
    bpy.context.scene.frame_set(40)
    yield 1.0
    capture("04_motion_done")
    bpy.ops.chimera.render_passes()
    yield from wait_stage(shot, "passes")
    bpy.ops.chimera.generate_video()
    yield from wait_stage(shot, "eval")
    yield 1.0
    capture("05_video_done")
    bpy.context.scene.frame_set(130)  # into shot 2: follow playhead
    yield 1.0
    capture("06_follow_playhead")


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
