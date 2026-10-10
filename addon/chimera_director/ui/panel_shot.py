import bpy

from .. import props, scene_io
from .common import STAGE_LABEL, SidebarPanel, stage_icon


class _ShotPanel(SidebarPanel):
    @classmethod
    def poll(cls, context):
        return context.scene.chimera.active_shot is not None


class CHIMERA_PT_shot(_ShotPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shot"
    bl_label = "Active Shot"

    def draw_header(self, context):
        self.layout.label(text=context.scene.chimera.active_shot.name)

    def draw(self, context):
        p = context.scene.chimera
        shot = p.active_shot
        layout = self.layout
        layout.prop(shot, "action_prompt", text="")
        row = layout.row(align=True)
        row.prop(shot, "duration_s")
        n = scene_io.frames_for(shot, p.fps)
        row.label(text=f"{n} f → {scene_io.wan_frame_count(n)}")
        layout.operator("chimera.view_shot", icon="CAMERA_DATA")

        box = layout.box()
        box.label(text="Characters", icon="COMMUNITY")
        row = box.row()
        row.template_list("CHIMERA_UL_characters", "", shot, "characters", shot,
                          "active_character_index", rows=2)
        col = row.column(align=True)
        col.operator("chimera.character_add", text="", icon="ADD")
        col.operator("chimera.character_remove", text="", icon="REMOVE")
        if shot.characters:
            char = shot.characters[min(shot.active_character_index, len(shot.characters) - 1)]
            col = box.column(align=True)
            row = col.row(align=True)
            row.prop(char, "rig", text="Library")
            row.operator("chimera.link_rig", text="", icon="LINK_BLEND")
            col.prop(char, "rig_object", text="Rig")
            col.prop(char, "lora")


class CHIMERA_PT_shot_motion(_ShotPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shot_motion"
    bl_label = "Motion"
    bl_parent_id = "CHIMERA_PT_shot"

    def draw(self, context):
        shot = context.scene.chimera.active_shot
        layout = self.layout
        row = layout.row(align=True)
        row.prop(shot, "motion_backend", text="")
        row.prop(shot, "motion_seed")
        row.prop(shot, "motion_variants", text="N")
        layout.prop(shot, "continuity")
        row = layout.row(align=True)
        row.operator("chimera.generate_motion", icon="ARMATURE_DATA")
        _cancel(row, shot, "motion")

        variants = set()
        for c in shot.characters:
            variants |= set(scene_io.variant_actions(shot.name, c.char_id))
        if variants:
            row = layout.row(align=True)
            row.label(text="Variant")
            for n in sorted(variants):
                op = row.operator("chimera.pick_variant", text=str(n), depress=(n == shot.selected_variant))
                op.variant = n
        row = layout.row()
        row.label(text=f"Source: {shot.motion_source}",
                  icon="GREASEPENCIL" if shot.motion_source == "manual" else "OUTLINER_OB_ARMATURE")

        col = layout.column(align=True)
        col.label(text="Targets (active character)", icon="EMPTY_ARROWS")
        row = col.row(align=True)
        row.operator("chimera.add_start", text="Start")
        row.operator("chimera.add_path", text="Path")
        row.operator("chimera.add_waypoint", text="Waypoint")
        col.operator_menu_enum("chimera.add_effector", "joint", text="Effector", icon="CON_KINEMATIC")


class CHIMERA_PT_shot_render(_ShotPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shot_render"
    bl_label = "Look & Render"
    bl_parent_id = "CHIMERA_PT_shot"

    def draw(self, context):
        shot = context.scene.chimera.active_shot
        layout = self.layout
        row = layout.row(align=True)
        row.prop(shot, "camera_mode", text="")
        if shot.camera_mode == "template":
            row.prop(shot, "camera_template", text="")
        layout.prop(shot, "look_prompt", text="Look")
        layout.prop(shot, "render_path", expand=True)
        if shot.render_path == "A":
            row = layout.row(align=True)
            row.prop(shot, "ctrl_pose", toggle=True)
            row.prop(shot, "ctrl_depth", toggle=True)
            row.prop(shot, "ctrl_line", toggle=True)
            layout.prop(shot, "steps")
        else:
            layout.prop(shot, "h3_mode")
            layout.prop(shot, "audio_prompt")
        row = layout.row(align=True)
        row.prop(shot, "resolution_x", text="W")
        row.prop(shot, "resolution_y", text="H")
        row = layout.row(align=True)
        row.operator("chimera.render_passes", icon="RENDERLAYERS")
        _cancel(row, shot, "passes")
        row = layout.row(align=True)
        row.operator("chimera.generate_video", icon="FILE_MOVIE")
        _cancel(row, shot, "generate")


class CHIMERA_PT_shot_status(_ShotPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shot_status"
    bl_label = "Status & Eval"
    bl_parent_id = "CHIMERA_PT_shot"

    def draw(self, context):
        shot = context.scene.chimera.active_shot
        layout = self.layout
        col = layout.column(align=True)
        for key in props.STAGES:
            st = shot.stages.get(key)
            row = col.row(align=True)
            row.label(text=STAGE_LABEL[key], icon=stage_icon(shot, key))
            if st is None:
                row.label(text="—")
                continue
            if st.status in props.ACTIVE_STATUSES:
                row.prop(st, "progress", text="", slider=True)
            else:
                row.label(text=st.status)
            if st.message:
                sub = col.row()
                sub.alert = True
                sub.label(text=st.message[:90])
        row = layout.row(align=True)
        row.label(text=f"PCK@0.1: {shot.pck:.3f}" if shot.pck >= 0 else "PCK@0.1: —")
        row.prop(shot, "identity_score")
        layout.label(text=f"Scene hash {shot.scene_hash[:10] or '—'}")


def _cancel(row, shot, stage):
    st = shot.stages.get(stage)
    if st and st.status in props.ACTIVE_STATUSES:
        row.operator("chimera.cancel_job", text="", icon="CANCEL").stage = stage


classes = (CHIMERA_PT_shot, CHIMERA_PT_shot_motion, CHIMERA_PT_shot_render, CHIMERA_PT_shot_status)
