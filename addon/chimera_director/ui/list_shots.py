import bpy

from .. import props
from .common import stage_icon


class CHIMERA_UL_shots(bpy.types.UIList):
    def draw_item(self, context, layout, data, shot, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=shot.name, icon="SEQUENCE")
        sub = row.row(align=True)
        sub.scale_x = 0.6
        sub.label(text=shot.action_prompt[:40] or "—")
        icons = row.row(align=True)
        for key in props.STAGES:
            icons.label(text="", icon=stage_icon(shot, key))
        if shot.pck >= 0:
            row.label(text=f"{shot.pck:.2f}")

    def filter_items(self, context, data, propname):
        shots = getattr(data, propname)
        order = sorted(range(len(shots)), key=lambda i: shots[i].order)
        neworder = [0] * len(shots)
        for pos, i in enumerate(order):
            neworder[i] = pos
        return [], neworder


class CHIMERA_UL_characters(bpy.types.UIList):
    def draw_item(self, context, layout, data, char, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(char, "char_id", text="", emboss=False, icon="ARMATURE_DATA")
        row.label(text=char.rig_object.name if char.rig_object else "no rig",
                  icon="CHECKMARK" if char.rig_object else "ERROR")


class CHIMERA_UL_bonemap(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.name)
        rig = data.bonemap_rig
        if rig is not None:
            row.prop_search(item, "bone", rig.data, "bones", text="")
        else:
            row.prop(item, "bone", text="")


classes = (CHIMERA_UL_shots, CHIMERA_UL_characters, CHIMERA_UL_bonemap)
