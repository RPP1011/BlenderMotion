import bpy

from .common import SidebarPanel


class CHIMERA_PT_project(SidebarPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_project"
    bl_label = "Director"

    def draw_header(self, context):
        p = context.scene.chimera
        self.layout.label(text="", icon="LINKED" if p.server_status == "ok" else "UNLINKED")

    def draw_header_preset(self, context):
        self.layout.label(text=context.scene.chimera.project_name)

    def draw(self, context):
        p = context.scene.chimera
        layout = self.layout
        if p.server_status != "ok":
            box = layout.box()
            box.alert = True
            box.label(text="Orchestrator not reachable", icon="ERROR")
            if p.server_detail:
                box.label(text=p.server_detail[:70])

        if context.workspace.name != "Director":
            layout.operator("chimera.setup_workspace", icon="WORKSPACE")

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(p, "brief", text="", icon="TEXT")
        row.operator("chimera.new_brief", text="", icon="ADD")
        sub = col.row(align=True)
        sub.scale_y = 1.3
        sub.operator("chimera.plan_from_brief", text="Plan Shots from Brief", icon="OUTLINER_OB_FONT")

        row = layout.row(align=True)
        row.prop(p, "follow_playhead", toggle=True, icon="PLAY")
        row.prop(p, "show_overlay", toggle=True, icon="OVERLAY")

        box = layout.box()
        box.prop(p, "show_settings", emboss=False,
                 icon="DISCLOSURE_TRI_DOWN" if p.show_settings else "DISCLOSURE_TRI_RIGHT")
        if p.show_settings:
            col = box.column()
            col.prop(p, "project_name")
            col.prop(p, "root")
            col.prop(p, "look_prompt")
            row = col.row(align=True)
            row.prop(p, "fps")
            row.prop(p, "resolution_x", text="W")
            row.prop(p, "resolution_y", text="H")
            row = col.row(align=True)
            row.operator("chimera.push", icon="EXPORT")
            row.operator("chimera.refresh_hash", text="", icon="FILE_REFRESH")


class CHIMERA_PT_shots(SidebarPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shots"
    bl_label = "Shots"

    def draw_header_preset(self, context):
        p = context.scene.chimera
        total = sum(s.duration_s for s in p.shots)
        self.layout.label(text=f"{len(p.shots)} · {total:.1f}s")

    def draw(self, context):
        p = context.scene.chimera
        row = self.layout.row()
        row.template_list("CHIMERA_UL_shots", "", p, "shots", p, "active_shot_index", rows=3)
        col = row.column(align=True)
        col.operator("chimera.shot_add", text="", icon="ADD")
        col.operator("chimera.shot_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("chimera.shot_move", text="", icon="TRIA_UP").direction = "UP"
        col.operator("chimera.shot_move", text="", icon="TRIA_DOWN").direction = "DOWN"
        row = self.layout.row(align=True)
        row.operator("chimera.layout", text="Re-layout", icon="SEQ_STRIP_DUPLICATE")
        row.operator("chimera.assemble_vse", text="Assemble", icon="SEQUENCE")
        row.operator("chimera.open_edit", text="", icon="FILE_MOVIE")


class CHIMERA_PT_bonemap(SidebarPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_bonemap"
    bl_label = "Bone Map"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        p = context.scene.chimera
        layout = self.layout
        layout.prop(p, "bonemap_rig")
        row = layout.row(align=True)
        row.operator("chimera.build_bonemap", icon="AUTO")
        row.operator("chimera.load_bonemap", text="", icon="FILE_FOLDER")
        row.operator("chimera.save_bonemap", text="", icon="FILE_TICK")
        if len(p.bonemap):
            layout.template_list("CHIMERA_UL_bonemap", "", p, "bonemap", p, "bonemap_index", rows=6)


class CHIMERA_PT_edit_scene(bpy.types.Panel):
    """Shown in the sequencer of the _EDIT scene: a way back."""
    bl_idname = "CHIMERA_PT_edit_scene"
    bl_label = "Director"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Director"

    @classmethod
    def poll(cls, context):
        return context.scene.name.endswith("_EDIT")

    def draw(self, context):
        self.layout.operator("chimera.back_to_scene", icon="LOOP_BACK")


classes = (CHIMERA_PT_project, CHIMERA_PT_shots, CHIMERA_PT_bonemap, CHIMERA_PT_edit_scene)
