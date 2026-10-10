import bpy

from .. import props, scene_io
from . import state, thumbs
from .common import STATUS_ICON, SidebarPanel, progress


def _width(context):
    return context.region.width if context.region else 300


class _ShotPanel(SidebarPanel):
    @classmethod
    def poll(cls, context):
        return context.scene.chimera.active_shot is not None


class CHIMERA_PT_shot(_ShotPanel, bpy.types.Panel):
    """The shot card: what it is, where it is in the pipeline, and the next step."""
    bl_idname = "CHIMERA_PT_shot"
    bl_label = ""

    def draw_header(self, context):
        self.layout.label(text=context.scene.chimera.active_shot.name.upper(), icon="SEQUENCE")

    def draw_header_preset(self, context):
        self.layout.operator("chimera.view_shot", text="", icon="CAMERA_DATA", emboss=False)

    def draw(self, context):
        p = context.scene.chimera
        shot = p.active_shot
        layout = self.layout

        icon = thumbs.icon_for(bpy.path.abspath(shot.thumb_path))
        if icon:
            layout.template_icon(icon_value=icon, scale=5.0)

        # Prompt, wrapped; the whole block opens the editor.
        box = layout.box()
        col = box.column(align=True)
        lines = state.wrap(shot.action_prompt, _width(context) - 70) or ["Describe the action…"]
        row = col.row()
        row.label(text=lines[0])
        row.operator("chimera.edit_prompt", text="", icon="GREASEPENCIL", emboss=False)
        for line in lines[1:5]:
            col.label(text=line)
        row = box.row(align=True)
        row.prop(shot, "duration_s", text="Duration")
        n = scene_io.frames_for(shot, p.fps)
        row.label(text=f"{n} f")

        # Pipeline chips.
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        for key, label, _icon in state.PIPELINE:
            st = shot.stages.get(key)
            s = st.status if st else "none"
            cell = grid.column(align=True)
            if st and s in props.ACTIVE_STATUSES:
                progress(cell, st.progress, label)
            else:
                cell.label(text=label, icon=STATUS_ICON[s])

        # The one next step.
        op, label, icon, hint = state.next_step(shot)
        col = layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.6
        if op:
            row.operator(op, text=label, icon=icon)
        else:
            row.box().label(text=label, icon=icon)
            job = state.active_job(shot)
            if job:
                row.operator("chimera.cancel_job", text="", icon="CANCEL").stage = job[0]
        hint_row = col.row()
        hint_row.scale_y = 0.8
        hint_row.label(text=hint, icon="INFO")

        for st in shot.stages:
            if st.status == "failed" and st.message:
                err = layout.box()
                err.alert = True
                for line in state.wrap(f"{st.name}: {st.message}", _width(context) - 50)[:4]:
                    err.label(text=line)
        for w in state.warnings(shot, p.fps):
            row = layout.row()
            row.alert = True
            row.label(text=w, icon="ERROR")


class CHIMERA_PT_shot_characters(_ShotPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shot_characters"
    bl_label = "Characters"
    bl_parent_id = "CHIMERA_PT_shot"

    def draw_header_preset(self, context):
        shot = context.scene.chimera.active_shot
        missing = sum(1 for c in shot.characters if c.rig_object is None)
        if missing:
            self.layout.label(text=f"{missing} without rig", icon="ERROR")

    def draw(self, context):
        shot = context.scene.chimera.active_shot
        layout = self.layout
        row = layout.row()
        row.template_list("CHIMERA_UL_characters", "", shot, "characters", shot,
                          "active_character_index", rows=2)
        col = row.column(align=True)
        col.operator("chimera.character_add", text="", icon="ADD")
        col.operator("chimera.character_remove", text="", icon="REMOVE")
        if shot.characters:
            char = shot.characters[min(shot.active_character_index, len(shot.characters) - 1)]
            col = layout.column()
            col.prop(char, "rig_object", text="Rig")
            row = col.row(align=True)
            row.prop(char, "rig", text="Library")
            row.operator("chimera.link_rig", text="", icon="LINK_BLEND")
            col.prop(char, "lora", text="LoRA")


class CHIMERA_PT_shot_motion(_ShotPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shot_motion"
    bl_label = "Motion"
    bl_parent_id = "CHIMERA_PT_shot"

    def draw_header_preset(self, context):
        shot = context.scene.chimera.active_shot
        if shot.motion_source == "manual":
            self.layout.label(text="edited", icon="GREASEPENCIL")

    def draw(self, context):
        shot = context.scene.chimera.active_shot
        layout = self.layout
        variants = set()
        for c in shot.characters:
            variants |= set(scene_io.variant_actions(shot.name, c.char_id))
        if variants:
            layout.label(text="Variants")
            grid = layout.grid_flow(row_major=True, columns=6, even_columns=True, align=True)
            for n in sorted(variants):
                op = grid.operator("chimera.pick_variant", text=f"v{n}", depress=(n == shot.selected_variant))
                op.variant = n
        row = layout.row(align=True)
        row.operator("chimera.generate_motion", text="Generate More" if variants else "Generate Motion",
                     icon="ARMATURE_DATA")
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(shot, "motion_backend", text="")
        row.prop(shot, "motion_variants", text="Count")
        col.prop(shot, "motion_seed")
        layout.prop(shot, "continuity")


class CHIMERA_PT_shot_targets(_ShotPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shot_targets"
    bl_label = "Targets"
    bl_parent_id = "CHIMERA_PT_shot"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        from .. import constraints
        n = len(constraints.helpers(context.scene.chimera.active_shot.name))
        if n:
            self.layout.label(text=str(n))

    def draw(self, context):
        from .. import constraints
        shot = context.scene.chimera.active_shot
        layout = self.layout
        if shot.characters:
            char = shot.characters[min(shot.active_character_index, len(shot.characters) - 1)]
            layout.label(text=f"For {char.char_id}, at the 3D cursor", icon="PIVOT_CURSOR")
        has_start = bool(shot.characters) and bool(constraints.helpers(
            shot.name, shot.characters[min(shot.active_character_index, len(shot.characters) - 1)].char_id,
            "start"))
        col = layout.column(align=True)
        col.operator("chimera.add_start", text="Move Start Here" if has_start else "Place Start",
                     icon="EMPTY_SINGLE_ARROW")
        sub = col.column(align=True)
        sub.enabled = has_start
        row = sub.row(align=True)
        row.operator("chimera.add_path", text="Path", icon="CURVE_PATH")
        row.operator("chimera.add_waypoint", text="Waypoint", icon="EMPTY_AXIS")
        sub.operator_menu_enum("chimera.add_effector", "joint", text="Hand / Foot Target", icon="CON_KINEMATIC")
        if not has_start:
            layout.label(text="Place Start first: it defines facing", icon="INFO")
        helpers = constraints.helpers(shot.name)
        if helpers:
            box = layout.box()
            col = box.column(align=True)
            for obj in sorted(helpers, key=lambda o: (o.get("chimera_role", ""), o.get("chimera_frame", 0))):
                row = col.row(align=True)
                role = obj.get("chimera_role", "")
                text = role
                if "chimera_frame" in obj:
                    text += f" @ f{obj['chimera_frame']}"
                if role == "effector":
                    text = f"{obj.get('chimera_joint', '')} @ f{obj.get('chimera_frame', 0)}"
                row.label(text=f"{obj.get('chimera_char', '')}: {text}",
                          icon={"start": "EMPTY_SINGLE_ARROW", "path": "CURVE_PATH",
                                "waypoint": "EMPTY_AXIS"}.get(role, "CON_KINEMATIC"))
                row.operator("object.select_pattern", text="", icon="RESTRICT_SELECT_OFF",
                             emboss=False).pattern = obj.name


class CHIMERA_PT_shot_video(_ShotPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shot_video"
    bl_label = "Camera & Video"
    bl_parent_id = "CHIMERA_PT_shot"

    def draw_header_preset(self, context):
        self.layout.label(text=f"H3 · {context.scene.chimera.active_shot.h3_mode.upper()}")

    def draw(self, context):
        shot = context.scene.chimera.active_shot
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        cam = bpy.data.objects.get(scene_io.cam_name(shot.name))
        if cam and cam.type == "CAMERA":
            layout.prop(cam.data, "lens", text="Lens")
        layout.prop(shot, "camera_mode", text="Camera")
        if shot.camera_mode == "template":
            layout.prop(shot, "camera_template", text="Template")
        layout.prop(shot, "look_prompt", text="Look")
        layout.prop(shot, "h3_mode", text="Mode")
        layout.prop(shot, "audio_prompt", text="Audio")
        col = layout.column(align=True)
        col.prop(shot, "resolution_x", text="Resolution X")
        col.prop(shot, "resolution_y", text="Y")
        layout.use_property_split = False
        row = layout.row(align=True)
        row.operator("chimera.render_passes", text="Render Passes", icon="RENDERLAYERS")
        row.operator("chimera.generate_video", text="Generate", icon="FILE_MOVIE")


class CHIMERA_PT_shot_status(_ShotPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shot_status"
    bl_label = "Review"
    bl_parent_id = "CHIMERA_PT_shot"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        shot = context.scene.chimera.active_shot
        if shot.pck >= 0:
            self.layout.label(text=f"PCK {shot.pck:.2f}")

    def draw(self, context):
        shot = context.scene.chimera.active_shot
        layout = self.layout
        if shot.pck >= 0:
            progress(layout, max(0.0, min(shot.pck, 1.0)), f"Pose accuracy PCK@0.1  {shot.pck:.3f}")
        else:
            layout.label(text="No pose score yet")
        layout.prop(shot, "identity_score", text="Identity (your score, 1–5)")
        col = layout.column(align=True)
        for key in props.STAGES:
            st = shot.stages.get(key)
            row = col.row(align=True)
            row.label(text=key.capitalize(), icon=STATUS_ICON[st.status if st else "none"])
            row.label(text=(st.status if st else "—").replace("_", " "))
            if st and st.status in props.ACTIVE_STATUSES:
                row.operator("chimera.cancel_job", text="", icon="CANCEL").stage = key
        row = layout.row()
        row.scale_y = 0.8
        row.label(text=f"Scene hash {shot.scene_hash[:12] or '—'}")
        layout.operator("chimera.pull_shot", icon="IMPORT")


classes = (CHIMERA_PT_shot, CHIMERA_PT_shot_characters, CHIMERA_PT_shot_motion, CHIMERA_PT_shot_targets,
           CHIMERA_PT_shot_video, CHIMERA_PT_shot_status)
