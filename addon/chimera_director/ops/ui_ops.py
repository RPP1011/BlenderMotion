"""Operators that exist for the UI: prompt dialog, workspace, review."""

import bpy
from bpy.props import StringProperty

from .. import scene_io
from .common import ShotOperator, active_shot, project

PROMPT_TIPS = (
    "One character, one continuous action, in plain words.",
    "Say how it ends if it matters: '... and lands in a crouch'.",
    "Not supported: interaction between characters, hands/fingers, face.",
)


class CHIMERA_OT_edit_prompt(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.edit_prompt"
    bl_label = "Action Prompt"
    bl_description = "Edit the shot's action prompt in a wide dialog"
    bl_options = {"REGISTER", "UNDO"}

    prompt: StringProperty(name="Action")

    def invoke(self, context, event):
        self.prompt = active_shot(context).action_prompt
        return context.window_manager.invoke_props_dialog(self, width=640)

    def draw(self, context):
        shot = active_shot(context)
        col = self.layout.column()
        col.label(text=f"{shot.name} · {shot.duration_s:.1f}s", icon="SEQUENCE")
        col.prop(self, "prompt", text="")
        tips = col.column(align=True)
        tips.scale_y = 0.8
        for tip in PROMPT_TIPS:
            tips.label(text=tip, icon="DOT")

    def execute(self, context):
        shot = active_shot(context)
        if shot.action_prompt != self.prompt:
            shot.action_prompt = self.prompt
            st = shot.stages.get("motion")
            if st and st.status == "done":
                st.status = "stale"
        return {"FINISHED"}


class CHIMERA_OT_select_shot(bpy.types.Operator):
    bl_idname = "chimera.select_shot"
    bl_label = "Select Shot"
    bl_options = {"INTERNAL"}
    shot: StringProperty()

    def execute(self, context):
        p = project(context)
        for i, s in enumerate(p.shots):
            if s.name == self.shot:
                p.active_shot_index = i
                return {"FINISHED"}
        return {"CANCELLED"}


class CHIMERA_OT_open_edit(bpy.types.Operator):
    bl_idname = "chimera.open_edit"
    bl_label = "Open Edit"
    bl_description = "Switch this window to the assembled edit (<scene>_EDIT)"

    def execute(self, context):
        scene = context.scene
        name = scene.name + "_EDIT"
        edit = bpy.data.scenes.get(name) or scene_io.edit_scene(scene)
        context.window.scene = edit
        self.report({"INFO"}, f"Switched to '{name}'. Switch back with the scene selector")
        return {"FINISHED"}


class CHIMERA_OT_back_to_scene(bpy.types.Operator):
    bl_idname = "chimera.back_to_scene"
    bl_label = "Back to Shots"
    bl_description = "Return from the edit scene to the animation scene"

    @classmethod
    def poll(cls, context):
        return context.scene.name.endswith("_EDIT") and context.scene.name[:-5] in bpy.data.scenes

    def execute(self, context):
        context.window.scene = bpy.data.scenes[context.scene.name[:-5]]
        return {"FINISHED"}


class CHIMERA_OT_setup_workspace(bpy.types.Operator):
    bl_idname = "chimera.setup_workspace"
    bl_label = "Director Workspace"
    bl_description = ("Create a 'Director' workspace: authoring viewport with the Director sidebar, "
                      "shot camera view, NLA and timeline with shot bands")

    def execute(self, context):
        existing = bpy.data.workspaces.get("Director")
        if existing is not None:
            context.window.workspace = existing
            return {"FINISHED"}
        layout_ws = bpy.data.workspaces.get("Layout") or context.workspace
        context.window.workspace = layout_ws
        before = set(bpy.data.workspaces)
        with context.temp_override(workspace=layout_ws):
            bpy.ops.workspace.duplicate()
        new = next(iter(set(bpy.data.workspaces) - before), None)
        if new is None:
            self.report({"ERROR"}, "Could not duplicate the Layout workspace")
            return {"CANCELLED"}
        new.name = "Director"
        window = context.window
        window.workspace = new
        # The new screen only exists after a redraw, and so do the areas each split
        # creates. Each step checks that the screen has caught up, else retries.
        steps = [_split_bottom, _split_top, _configure_views, _open_director_tab]
        state = {"i": 0, "tries": 0}

        def tick():
            if state["i"] >= len(steps) or state["tries"] > 50:
                if state["tries"] > 50:
                    print(f"[chimera] workspace layout gave up at {steps[state['i']].__name__}")
                return None
            try:
                done = steps[state["i"]](window)
            except Exception as e:  # layout is cosmetic; never leave a half-dead timer
                print(f"[chimera] workspace layout step {steps[state['i']].__name__} failed: {e!r}")
                return None
            if done:
                state["i"] += 1
                state["tries"] = 0
            else:
                state["tries"] += 1
            return 0.1

        bpy.app.timers.register(tick, first_interval=0.1)
        return {"FINISHED"}


def _window_region(area):
    return next(r for r in area.regions if r.type == "WINDOW")


def _split(window, area, direction, factor):
    """Split `area`; return the newly created area."""
    before = set(window.screen.areas)
    with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=_window_region(area)):
        bpy.ops.screen.area_split(direction=direction, factor=factor)
    return next(iter(set(window.screen.areas) - before))


# Steps return True when done, False to be retried after another redraw.

def _views(window):
    return [a for a in window.screen.areas if a.type == "VIEW_3D"]


def _split_bottom(window):
    views = _views(window)
    if not views or window.workspace.name != "Director":
        return False
    main = max(views, key=lambda a: a.width * a.height)
    _split(window, main, "HORIZONTAL", 0.3)
    return True


def _split_top(window):
    views = sorted(_views(window), key=lambda a: a.y)
    if len(views) < 2 or views[0].y == views[-1].y:
        return False
    lower, upper = views[0], views[-1]
    # Bottom third: NLA, where each shot's motion strip lives (and shot bands draw).
    lower.type = "NLA_EDITOR"
    _split(window, upper, "VERTICAL", 0.5)
    return True


def _configure_views(window):
    views = sorted(_views(window), key=lambda a: a.x)
    if len(views) < 2 or views[0].x == views[-1].x:
        return False
    left, right = views[0], views[-1]
    cam = right.spaces.active
    cam.region_3d.view_perspective = "CAMERA"
    cam.show_region_ui = False
    cam.show_region_toolbar = False
    cam.overlay.show_extras = False
    left.spaces.active.show_region_ui = True
    for area in window.screen.areas:
        if area.type == "NLA_EDITOR":
            area.spaces.active.show_region_ui = False
    return True


def _open_director_tab(window):
    for area in _views(window):
        if area.spaces.active.show_region_ui:
            for region in area.regions:
                if region.type == "UI" and region.width > 1:
                    region.active_panel_category = "Director"
                    return True
    return False


classes = (CHIMERA_OT_edit_prompt, CHIMERA_OT_select_shot, CHIMERA_OT_open_edit, CHIMERA_OT_back_to_scene,
           CHIMERA_OT_setup_workspace)
