import bpy
from bpy.props import IntProperty

from .. import constraints, prefs, scene_io
from ..client import OrchestratorError
from . import rig_map
from .common import ShotOperator, active_shot, get_client, project, push_shot, report_error, track_job


class CHIMERA_OT_generate_motion(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.generate_motion"
    bl_label = "Generate Motion"
    bl_description = ("Generate motion variants for the active shot. Results are added as new "
                      "variants; existing variants and manual edits are never overwritten")

    def execute(self, context):
        p = project(context)
        shot = active_shot(context)
        if not shot.action_prompt.strip():
            self.report({"ERROR"}, "Shot has no action prompt")
            return {"CANCELLED"}
        if not shot.characters:
            self.report({"ERROR"}, "Shot has no characters")
            return {"CANCELLED"}
        pr = prefs.get()
        payload, warnings = constraints.export(context.scene, p, shot,
                                               pr.soma_hip_height if pr else 0.95)
        for w in warnings:
            self.report({"WARNING"}, w)
        payload["scene"] = context.scene.name
        for c in shot.characters:  # rest pose + bone map for the retarget worker
            rig = c.rig_object
            if rig is not None and rig.type == "ARMATURE" and c.char_id in payload["characters"]:
                payload["characters"][c.char_id]["rig"] = constraints.rig_payload(
                    rig, rig_map.load_or_guess(p, rig))
        payload["variant_offset"] = scene_io.next_variant_index(
            shot.name, [c.char_id for c in shot.characters])
        try:
            push_shot(context, shot)
            resp = get_client().motion(p.project_name, shot.name, payload)
            track_job(context, shot, "motion", resp)
        except OrchestratorError as e:
            return report_error(self, e)
        shot.stage("retarget").status = "queued"
        return {"FINISHED"}


class CHIMERA_OT_pick_variant(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.pick_variant"
    bl_label = "Use Variant"
    bl_description = "Put this motion variant on the shot's NLA track"
    bl_options = {"REGISTER", "UNDO"}
    variant: IntProperty(min=0)

    def execute(self, context):
        shot = active_shot(context)
        n = scene_io.assign_variant(context.scene, shot, self.variant)
        if not n:
            self.report({"WARNING"}, "No rig/action for that variant (link the rig first?)")
            return {"CANCELLED"}
        acts = [scene_io.variant_actions(shot.name, c.char_id).get(self.variant) for c in shot.characters]
        manual = any(a is not None and a.get("chimera_import_hash")
                     and scene_io.action_hash(a) != a["chimera_import_hash"] for a in acts)
        shot.motion_source = "manual" if manual else "generated"
        return {"FINISHED"}


classes = (CHIMERA_OT_generate_motion, CHIMERA_OT_pick_variant)
