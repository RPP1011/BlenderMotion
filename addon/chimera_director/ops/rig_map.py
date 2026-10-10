"""Bone map authoring (docs §4.2): canonical joint -> rig bone, saved per rig.

The canonical joint list below is a semantic humanoid core, NOT the SOMA-77
joint names (not available here). The motion server / retarget worker maps
SOMA-77 onto these keys; replace the list once the SOMA names are pinned.
"""

import json
import os
import re

import bpy

from .common import project

CANONICAL_JOINTS = [
    "pelvis", "spine1", "spine2", "spine3", "neck", "head",
    "l_clavicle", "l_shoulder", "l_elbow", "l_wrist",
    "r_clavicle", "r_shoulder", "r_elbow", "r_wrist",
    "l_hip", "l_knee", "l_ankle", "l_toe",
    "r_hip", "r_knee", "r_ankle", "r_toe",
]

# Per joint: list of regex alternatives matched against a normalized bone name.
_PATTERNS = {
    "pelvis": [r"^(pelvis|hips?|root_?hips?)$", r"^spine$"],
    "neck": [r"^neck0?1?$"],
    "head": [r"^head$"],
    "clavicle": [r"^(clavicle|shoulder)$"],
    "shoulder": [r"^(upperarm|arm)$"],
    "elbow": [r"^(lowerarm|forearm)$"],
    "wrist": [r"^hand$"],
    "hip": [r"^(thigh|upleg|upperleg)$"],
    "knee": [r"^(calf|shin|leg|lowerleg)$"],
    "ankle": [r"^foot$"],
    "toe": [r"^(toes?|toebase|ball)$"],
}
_SIDE_RES = [
    (re.compile(r"^(left|l)[_.\s-]?|[_.\s-](left|l)$", re.I), "l"),
    (re.compile(r"^(right|r)[_.\s-]?|[_.\s-](right|r)$", re.I), "r"),
]


def _split(bone_name):
    """Return (side or None, normalized core name)."""
    n = bone_name.split(":")[-1]
    n = re.sub(r"^(def|org|mch)[-_.]", "", n, flags=re.I)
    side = None
    m = re.search(r"(Left|Right)", n)
    if m:
        side = m.group(1)[0].lower()
        n = n.replace(m.group(1), "")
    else:
        for rx, s in _SIDE_RES:
            if rx.search(n):
                side = s
                n = rx.sub("", n)
                break
    return side, re.sub(r"[^a-z0-9]", "", n.lower())


_SPINE_RX = re.compile(r"^(spine\d*|torso|chest|upperchest)$")


def guess(bone_names):
    """Best-effort name match. Spine joints are assigned in bone order (hierarchy
    order in practice), because numbering differs per rig: Mixamo starts at
    Spine, others at spine_01, Rigify's DEF-spine is the pelvis."""
    parsed = [(b, *_split(b)) for b in bone_names]
    result = {}
    for joint in CANONICAL_JOINTS:
        if joint.startswith("spine"):
            continue
        side, key = (joint[0], joint[2:]) if joint[1] == "_" else (None, joint)
        for rx in _PATTERNS.get(key, []):
            hit = next((b for b, s, core in parsed if s == side and re.match(rx, core)
                        and b not in result.values()), None)
            if hit:
                result[joint] = hit
                break
    chain = [b for b, side, core in parsed
             if side is None and _SPINE_RX.match(core) and b not in result.values()]
    for joint, bone in zip(("spine1", "spine2", "spine3"), chain):
        result[joint] = bone
    return result


def bonemap_path(p, rig):
    return os.path.join(bpy.path.abspath(p.root), "rigs", f"{bpy.path.clean_name(rig.name)}.bonemap.json")


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
