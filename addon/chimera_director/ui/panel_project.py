import bpy

from .common import SidebarPanel


class CHIMERA_PT_project(SidebarPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_project"
    bl_label = "Project"

    def draw(self, context):
        p = context.scene.chimera
        layout = self.layout
        row = layout.row()
        ok = p.server_status == "ok"
        row.label(text=f"Orchestrator: {p.server_status}", icon="LINKED" if ok else "UNLINKED")
        if p.server_detail:
            col = layout.column(align=True)
            col.scale_y = 0.7
            col.label(text=p.server_detail[:80])
        col = layout.column(align=True)
        col.prop(p, "project_name")
        col.prop(p, "root")
        row = layout.row(align=True)
        row.prop(p, "brief")
        row.operator("chimera.new_brief", text="", icon="ADD")
        layout.operator("chimera.plan_from_brief", icon="OUTLINER_OB_FONT")
        layout.prop(p, "look_prompt")
        row = layout.row(align=True)
        row.prop(p, "fps")
        row.prop(p, "resolution_x", text="W")
        row.prop(p, "resolution_y", text="H")
        row = layout.row(align=True)
        row.operator("chimera.push", icon="EXPORT")
        row.operator("chimera.refresh_hash", text="", icon="FILE_REFRESH")


class CHIMERA_PT_shots(SidebarPanel, bpy.types.Panel):
    bl_idname = "CHIMERA_PT_shots"
    bl_label = "Shots"

    def draw(self, context):
        p = context.scene.chimera
        row = self.layout.row()
        row.template_list("CHIMERA_UL_shots", "", p, "shots", p, "active_shot_index", rows=4)
        col = row.column(align=True)
        col.operator("chimera.shot_add", text="", icon="ADD")
        col.operator("chimera.shot_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("chimera.shot_move", text="", icon="TRIA_UP").direction = "UP"
        col.operator("chimera.shot_move", text="", icon="TRIA_DOWN").direction = "DOWN"
        row = self.layout.row(align=True)
        row.operator("chimera.layout", icon="SEQ_STRIP_DUPLICATE")
        row.operator("chimera.assemble_vse", icon="SEQUENCE")


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


classes = (CHIMERA_PT_project, CHIMERA_PT_shots, CHIMERA_PT_bonemap)
