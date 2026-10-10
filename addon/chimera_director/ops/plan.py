import bpy
from bpy.props import BoolProperty

from .. import contract, scene_io
from ..client import OrchestratorError
from .common import get_client, project, report_error


class CHIMERA_OT_plan_from_brief(bpy.types.Operator):
    bl_idname = "chimera.plan_from_brief"
    bl_label = "Plan Shots from Brief"
    bl_description = "Ask the planner LLM for a shot list. Manual only; never re-plans on its own"
    bl_options = {"REGISTER", "UNDO"}

    replace_existing: BoolProperty(
        name="Overwrite existing shots",
        description="Overwrite shots whose id the planner returns again. Off: those drafts are skipped",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.scene.chimera.brief is not None

    def invoke(self, context, event):
        if len(project(context).shots):
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def execute(self, context):
        p = project(context)
        brief = p.brief.as_string().strip()
        if not brief:
            self.report({"ERROR"}, "Brief is empty")
            return {"CANCELLED"}
        chars = {}
        for s in p.shots:
            for c in s.characters:
                chars.setdefault(c.char_id, {"char_id": c.char_id, "rig": c.rig})
        try:
            resp = get_client().plan(p.project_name, brief, list(chars.values()))
        except OrchestratorError as e:
            return report_error(self, e)
        added = skipped = 0
        for draft in resp.get("shots", []):
            sid = draft.get("id")
            if not sid:
                continue
            shot = p.find_shot(sid)
            if shot is not None and not self.replace_existing:
                skipped += 1
                continue
            if shot is None:
                shot = p.shots.add()
                shot.name = sid
            contract.dict_to_shot(draft, shot, p)
            # Re-attach rigs already linked for the same character.
            for c in shot.characters:
                for other in p.shots:
                    for oc in other.characters:
                        if oc.char_id == c.char_id and oc.rig_object and c.rig_object is None:
                            c.rig_object = oc.rig_object
            scene_io.ensure_shot_collection(context.scene, shot.name)
            scene_io.ensure_camera(context.scene, shot)
            added += 1
        scene_io.layout_shots(context.scene, p)
        self.report({"INFO"}, f"Planner: {added} shots written, {skipped} existing kept")
        return {"FINISHED"}


classes = (CHIMERA_OT_plan_from_brief,)
