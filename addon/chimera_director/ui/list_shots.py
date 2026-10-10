import bpy

from . import state
from .common import STATUS_ICON


class CHIMERA_UL_shots(bpy.types.UIList):
    def draw_item(self, context, layout, data, shot, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        overall = state.overall(shot)
        row.label(text="", icon=STATUS_ICON[overall] if overall != "none" else "SEQUENCE")
        row.label(text=shot.name)
        mid = row.row()
        mid.scale_x = 2.5
        mid.label(text=shot.action_prompt or "—")
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        job = state.active_job(shot)
        if job:
            sub.label(text=f"{job[1]} {int(job[2].progress * 100)}%")
        elif shot.pck >= 0:
            sub.label(text=f"PCK {shot.pck:.2f}")
        sub.label(text=f"{shot.duration_s:.1f}s")

    def filter_items(self, context, data, propname):
        shots = getattr(data, propname)
        order = sorted(range(len(shots)), key=lambda i: shots[i].order)
        neworder = [0] * len(shots)
        for pos, i in enumerate(order):
            neworder[i] = pos
        flags = []
        if self.filter_name:
            needle = self.filter_name.lower()
            flags = [self.bitflag_filter_item if needle in (s.name + " " + s.action_prompt).lower() else 0
                     for s in shots]
        return flags, neworder


class CHIMERA_UL_characters(bpy.types.UIList):
    def draw_item(self, context, layout, data, char, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(char, "char_id", text="", emboss=False, icon="ARMATURE_DATA")
        row.label(text=char.rig_object.name if char.rig_object else "no rig",
                  icon="CHECKMARK" if char.rig_object else "ERROR")


class CHIMERA_UL_bonemap(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.name, icon="BONE_DATA" if item.bone else "ERROR")
        rig = data.bonemap_rig
        if rig is not None:
            row.prop_search(item, "bone", rig.data, "bones", text="")
        else:
            row.prop(item, "bone", text="")


classes = (CHIMERA_UL_shots, CHIMERA_UL_characters, CHIMERA_UL_bonemap)
