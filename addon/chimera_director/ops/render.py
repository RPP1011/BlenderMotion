"""Stage 4 (passes) and stage 5 (generate) submission."""

import bpy

from .. import scene_io, snapshot, sync
from ..compat import vse_strips
from ..client import OrchestratorError
from .common import ShotOperator, active_shot, get_client, project, push_shot, report_error, track_job


class CHIMERA_OT_render_passes(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.render_passes"
    bl_label = "Render Control Passes"
    bl_description = "Snapshot the file and have a headless Blender worker render the shot's control passes"

    def execute(self, context):
        p = project(context)
        scene = context.scene
        shot = active_shot(context)
        rng = scene_io.shot_range(scene, shot.name)
        if rng is None:
            self.report({"ERROR"}, "Shot has no in/out markers; run Re-layout")
            return {"CANCELLED"}
        if not any(True for _ in scene_io.shot_strips(shot)):
            self.report({"WARNING"}, "No motion on this shot's NLA track; passes will show a static rig")
        sync.refresh_hashes(scene)
        h = shot.scene_hash
        try:
            snap = snapshot.take(p, shot.name, h)
        except RuntimeError as e:
            return report_error(self, e)
        n = rng[1] - rng[0]
        body = {
            "snapshot_path": snap,
            "scene_hash": h,
            "scene": scene.name,
            "frame_in": rng[0],
            "frame_out": rng[1],
            "frames": n,
            "frames_wan": scene_io.wan_frame_count(n),
            "camera": scene_io.cam_name(shot.name),
            "characters": {c.char_id: c.rig_object.name for c in shot.characters if c.rig_object},
        }
        try:
            push_shot(context, shot)
            track_job(context, shot, "passes", get_client().passes(p.project_name, shot.name, body))
        except OrchestratorError as e:
            return report_error(self, e)
        shot.stage("passes").input_hash = h
        return {"FINISHED"}


class CHIMERA_OT_generate_video(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.generate_video"
    bl_label = "Generate Video"
    bl_description = "Run the shot's render path in ComfyUI on the latest control passes"

    def execute(self, context):
        p = project(context)
        shot = active_shot(context)
        passes = shot.stages.get("passes")
        if passes is None or passes.status != "done":
            state = passes.status if passes else "not run"
            self.report({"ERROR"}, f"Control passes are {state}; render passes first")
            return {"CANCELLED"}
        try:
            push_shot(context, shot)
            track_job(context, shot, "generate",
                      get_client().generate(p.project_name, shot.name, shot.render_path))
        except OrchestratorError as e:
            return report_error(self, e)
        shot.stage("generate").input_hash = passes.input_hash
        shot.stage("eval").status = "queued"
        return {"FINISHED"}


class CHIMERA_OT_assemble_vse(bpy.types.Operator):
    bl_idname = "chimera.assemble_vse"
    bl_label = "Assemble Edit"
    bl_description = "Move every shot's strips in the _EDIT scene to the shot's in-marker"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        edit = scene_io.edit_scene(scene)
        strips = vse_strips(edit.sequence_editor)
        moved = 0
        for shot in project(context).shots:
            rng = scene_io.shot_range(scene, shot.name)
            if rng is None:
                continue
            for prefix in ("PB", "VID", "AUD"):
                s = strips.get(f"{prefix}_{shot.name}")
                if s is not None and s.frame_start != rng[0]:
                    s.frame_start = rng[0]
                    moved += 1
        self.report({"INFO"}, f"Edit scene '{edit.name}': {moved} strips moved")
        return {"FINISHED"}


classes = (CHIMERA_OT_render_passes, CHIMERA_OT_generate_video, CHIMERA_OT_assemble_vse)
