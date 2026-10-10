"""Bone map authoring (docs §4.2): canonical joint -> rig bone, saved per rig.

The joint list and the name guess live in ../bonemap.py.
"""

import json
import os

import bpy

from ..bonemap import CANONICAL_JOINTS, guess  # noqa: F401
from .common import project


def bonemap_path(p, rig):
    return os.path.join(bpy.path.abspath(p.root), "rigs", f"{bpy.path.clean_name(rig.name)}.bonemap.json")


def load_or_guess(p, rig):
    """The rig's saved bone map, or the name guess if none was saved."""
    path = bonemap_path(p, rig) if p.root else ""
    if path and os.path.isfile(path):
        with open(path) as f:
            return json.load(f).get("map", {})
    return guess([b.name for b in rig.data.bones])


class CHIMERA_OT_build_bonemap(bpy.types.Operator):
    bl_idname = "chimera.build_bonemap"
    bl_label = "Auto-map Bones"
    bl_description = "Guess the canonical joint -> rig bone mapping from bone names"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene.chimera.bonemap_rig is not None

    def execute(self, context):
        p = project(context)
        mapping = guess([b.name for b in p.bonemap_rig.data.bones])
        p.bonemap.clear()
        for joint in CANONICAL_JOINTS:
            item = p.bonemap.add()
            item.name = joint
            item.bone = mapping.get(joint, "")
        missing = [j for j in CANONICAL_JOINTS if j not in mapping]
        msg = f"Mapped {len(mapping)}/{len(CANONICAL_JOINTS)}"
        if missing:
            msg += f"; fix: {', '.join(missing)}"
        self.report({"WARNING"} if missing else {"INFO"}, msg)
        return {"FINISHED"}


class CHIMERA_OT_save_bonemap(bpy.types.Operator):
    bl_idname = "chimera.save_bonemap"
    bl_label = "Save Bone Map"

    @classmethod
    def poll(cls, context):
        p = context.scene.chimera
        return p.bonemap_rig is not None and len(p.bonemap) and p.root

    def execute(self, context):
        p = project(context)
        path = bonemap_path(p, p.bonemap_rig)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"rig": p.bonemap_rig.name, "skeleton": "canonical-v0",
                       "map": {i.name: i.bone for i in p.bonemap if i.bone}}, f, indent=2)
        self.report({"INFO"}, f"Wrote {path}")
        return {"FINISHED"}


class CHIMERA_OT_load_bonemap(bpy.types.Operator):
    bl_idname = "chimera.load_bonemap"
    bl_label = "Load Bone Map"

    @classmethod
    def poll(cls, context):
        p = context.scene.chimera
        return p.bonemap_rig is not None and p.root

    def execute(self, context):
        p = project(context)
        path = bonemap_path(p, p.bonemap_rig)
        if not os.path.isfile(path):
            self.report({"ERROR"}, f"No bone map at {path}")
            return {"CANCELLED"}
        with open(path) as f:
            mapping = json.load(f).get("map", {})
        p.bonemap.clear()
        for joint in CANONICAL_JOINTS:
            item = p.bonemap.add()
            item.name = joint
            item.bone = mapping.get(joint, "")
        return {"FINISHED"}


classes = (CHIMERA_OT_build_bonemap, CHIMERA_OT_save_bonemap, CHIMERA_OT_load_bonemap)
