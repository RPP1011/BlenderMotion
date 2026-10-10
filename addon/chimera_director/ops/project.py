import bpy
from bpy.props import EnumProperty, StringProperty

from .. import contract, events, props, scene_io, sync
from .common import ShotOperator, active_shot, get_client, project, report_error
from ..client import OrchestratorError

VALID_STATUSES = {i[0] for i in props.STATUS_ITEMS}


class CHIMERA_OT_shot_add(bpy.types.Operator):
    bl_idname = "chimera.shot_add"
    bl_label = "Add Shot"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p = project(context)
        prev = p.active_shot
        order = max((s.order for s in p.shots), default=0) + 10
        shot_id = f"sh_{order:03d}"
        while p.find_shot(shot_id):
            order += 10
            shot_id = f"sh_{order:03d}"
        shot = p.shots.add()
        shot.name = shot_id
        shot.order = order
        shot.resolution_x, shot.resolution_y = p.resolution_x, p.resolution_y
        if prev is not None:
            for c in prev.characters:
                nc = shot.characters.add()
                nc.char_id, nc.rig, nc.rig_object, nc.lora = c.char_id, c.rig, c.rig_object, c.lora
                for r in c.refs:
                    nc.refs.add().path = r.path
        else:
            shot.characters.add().char_id = "hero"
        scene_io.ensure_shot(context.scene, p, shot)
        p.active_shot_index = len(p.shots) - 1
        return {"FINISHED"}


class CHIMERA_OT_shot_remove(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.shot_remove"
    bl_label = "Remove Shot"
    bl_description = "Remove the shot, its markers, camera and helpers (motion actions are kept)"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        p = project(context)
        shot = active_shot(context)
        scene_io.remove_shot(context.scene, shot.name)
        p.shots.remove(p.active_shot_index)
        p.active_shot_index = max(0, p.active_shot_index - 1)
        scene_io.layout_shots(context.scene, p)
        return {"FINISHED"}


class CHIMERA_OT_shot_move(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.shot_move"
    bl_label = "Move Shot"
    bl_options = {"REGISTER", "UNDO"}
    direction: EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", "")])

    def execute(self, context):
        p = project(context)
        ordered = sorted(p.shots, key=lambda s: s.order)
        shot = active_shot(context)
        i = ordered.index(shot)
        j = i - 1 if self.direction == "UP" else i + 1
        if not 0 <= j < len(ordered):
            return {"CANCELLED"}
        ordered[i].order, ordered[j].order = ordered[j].order, ordered[i].order
        scene_io.layout_shots(context.scene, p)
        return {"FINISHED"}


class CHIMERA_OT_layout(bpy.types.Operator):
    bl_idname = "chimera.layout"
    bl_label = "Re-layout Timeline"
    bl_description = "Pack shot markers end to end from shot order and durations"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p = project(context)
        for shot in p.shots:
            scene_io.ensure_shot_collection(context.scene, shot.name)
            scene_io.ensure_camera(context.scene, shot)
        scene_io.layout_shots(context.scene, p)
        return {"FINISHED"}


class CHIMERA_OT_view_shot(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.view_shot"
    bl_label = "Frame Shot"
    bl_description = "Set the preview range to the active shot and look through its camera"

    def execute(self, context):
        scene = context.scene
        shot = active_shot(context)
        rng = scene_io.shot_range(scene, shot.name)
        if rng is None:
            self.report({"WARNING"}, "Shot has no markers; run Re-layout")
            return {"CANCELLED"}
        scene.use_preview_range = True
        scene.frame_preview_start, scene.frame_preview_end = rng[0], rng[1] - 1
        scene.frame_set(rng[0])
        cam = bpy.data.objects.get(scene_io.cam_name(shot.name))
        if cam:
            scene.camera = cam
        return {"FINISHED"}


class CHIMERA_OT_character_add(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.character_add"
    bl_label = "Add Character"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        shot = active_shot(context)
        c = shot.characters.add()
        c.char_id = f"char{len(shot.characters)}"
        shot.active_character_index = len(shot.characters) - 1
        if len(shot.characters) > 1:
            self.report({"WARNING"}, "Multi-character interaction is a v1 non-goal; "
                                     "characters are generated independently")
        return {"FINISHED"}


class CHIMERA_OT_character_remove(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.character_remove"
    bl_label = "Remove Character"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        shot = active_shot(context)
        if shot.characters:
            shot.characters.remove(shot.active_character_index)
            shot.active_character_index = max(0, shot.active_character_index - 1)
        return {"FINISHED"}


class CHIMERA_OT_link_rig(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.link_rig"
    bl_label = "Link Rig"
    bl_description = "Link the character's rig from its library .blend and make a library override"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p = project(context)
        shot = active_shot(context)
        if not shot.characters:
            return {"CANCELLED"}
        char = shot.characters[shot.active_character_index]
        try:
            rig = scene_io.link_rig(context.scene, p.root, char.rig)
        except (ValueError, FileNotFoundError, KeyError, RuntimeError) as e:
            return report_error(self, e)
        for s in p.shots:  # same character id -> same rig across shots
            for c in s.characters:
                if c.char_id == char.char_id and c.rig == char.rig:
                    c.rig_object = rig
        self.report({"INFO"}, f"Linked {rig.name}")
        return {"FINISHED"}


class CHIMERA_OT_push(bpy.types.Operator):
    bl_idname = "chimera.push"
    bl_label = "Sync to Orchestrator"
    bl_description = "Send project settings and every shot's JSON to the orchestrator"

    def execute(self, context):
        p = project(context)
        c = get_client()
        try:
            c.put_project(p.project_name, {**contract.project_to_dict(p), "root": bpy.path.abspath(p.root)})
            for shot in p.shots:
                c.put_shot(p.project_name, shot.name, contract.shot_to_dict(shot, p))
        except OrchestratorError as e:
            return report_error(self, e)
        self.report({"INFO"}, f"Synced {len(p.shots)} shots")
        return {"FINISHED"}


class CHIMERA_OT_pull_shot(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.pull_shot"
    bl_label = "Pull Shot State"
    bl_description = "Fetch the shot JSON and per-stage status from the orchestrator"

    def execute(self, context):
        p = project(context)
        shot = active_shot(context)
        try:
            data = get_client().get_shot(p.project_name, shot.name)
        except OrchestratorError as e:
            return report_error(self, e)
        if "shot" in data:
            contract.dict_to_shot(data["shot"], shot, p)
        for key, st in (data.get("stages") or {}).items():
            s = shot.stage(key)
            if st.get("status") in VALID_STATUSES:
                s.status = st["status"]
            if st.get("job_id"):
                s.job_id = st["job_id"]
        return {"FINISHED"}


class CHIMERA_OT_new_brief(bpy.types.Operator):
    bl_idname = "chimera.new_brief"
    bl_label = "New Brief"
    bl_description = "Create a text block for the brief (edit it in the Text Editor)"

    def execute(self, context):
        p = project(context)
        txt = bpy.data.texts.new(f"{p.project_name}_brief")
        txt.write("Describe the film: characters, setting, beats...\n")
        p.brief = txt
        return {"FINISHED"}


class CHIMERA_OT_refresh_hash(bpy.types.Operator):
    bl_idname = "chimera.refresh_hash"
    bl_label = "Refresh Hashes"
    bl_description = "Recompute per-shot scene hashes now and mark changed stages stale"

    def execute(self, context):
        sync.refresh_hashes(context.scene)
        return {"FINISHED"}


class CHIMERA_OT_cancel_job(ShotOperator, bpy.types.Operator):
    bl_idname = "chimera.cancel_job"
    bl_label = "Cancel Job"
    stage: StringProperty()

    def execute(self, context):
        st = active_shot(context).stages.get(self.stage)
        if not st or not st.job_id:
            return {"CANCELLED"}
        try:
            get_client().cancel(st.job_id)
        except OrchestratorError as e:
            return report_error(self, e)
        events.WATCHER.watch(st.job_id, context.scene.name, active_shot(context).name, self.stage)
        return {"FINISHED"}


classes = (
    CHIMERA_OT_shot_add, CHIMERA_OT_shot_remove, CHIMERA_OT_shot_move, CHIMERA_OT_layout,
    CHIMERA_OT_view_shot, CHIMERA_OT_character_add, CHIMERA_OT_character_remove,
    CHIMERA_OT_link_rig, CHIMERA_OT_push, CHIMERA_OT_pull_shot, CHIMERA_OT_new_brief,
    CHIMERA_OT_refresh_hash, CHIMERA_OT_cancel_job,
)
