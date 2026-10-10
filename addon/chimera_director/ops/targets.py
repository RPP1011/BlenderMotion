"""Operators that author spatial targets (docs §4.1a)."""

import bpy
from bpy.props import BoolProperty, EnumProperty
from mathutils import Vector

from .. import constraints, scene_io
from .common import ShotOperator, active_shot


def _active_char(shot):
    if not shot.characters:
        return None
    return shot.characters[min(shot.active_character_index, len(shot.characters) - 1)]


def _tag(obj, shot, char, role, frame=None):
    obj["chimera_shot"] = shot.name
    obj["chimera_char"] = char.char_id
    obj["chimera_role"] = role
    if frame is not None:
        obj["chimera_frame"] = int(frame)


def _link(context, shot, obj):
    scene_io.ensure_shot_collection(context.scene, shot.name).objects.link(obj)
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _local_frame(context, shot):
    rng = scene_io.shot_range(context.scene, shot.name)
    return context.scene.frame_current - (rng[0] if rng else context.scene.frame_start)


class _TargetOp(ShotOperator):
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        shot = active_shot(context)
        return shot is not None and len(shot.characters) > 0 and context.mode == "OBJECT"


class CHIMERA_OT_add_start(_TargetOp, bpy.types.Operator):
    bl_idname = "chimera.add_start"
    bl_label = "Start Marker"
    bl_description = "Root start position and facing (+Y) for the active character, at the 3D cursor"

    def execute(self, context):
        shot = active_shot(context)
        char = _active_char(shot)
        for old in constraints.helpers(shot.name, char.char_id, "start"):
            bpy.data.objects.remove(old)
        obj = bpy.data.objects.new(f"START_{shot.name}_{char.char_id}", None)
        obj.empty_display_type = "ARROWS"  # local +Y is the facing direction
        obj.empty_display_size = 1.0
        obj.location = context.scene.cursor.location
        _tag(obj, shot, char, "start")
        _link(context, shot, obj)
        return {"FINISHED"}


class CHIMERA_OT_add_path(_TargetOp, bpy.types.Operator):
    bl_idname = "chimera.add_path"
    bl_label = "Root Path"
    bl_description = "Dense root path for the active character, sampled per frame by arc length"

    def execute(self, context):
        shot = active_shot(context)
        char = _active_char(shot)
        starts = constraints.helpers(shot.name, char.char_id, "start")
        origin = starts[0].matrix_world if starts else None
        curve = bpy.data.curves.new(f"PATH_{shot.name}_{char.char_id}", "CURVE")
        curve.dimensions = "3D"
        spline = curve.splines.new("BEZIER")
        spline.bezier_points.add(1)
        for i, y in enumerate((0.0, 4.0)):
            bp = spline.bezier_points[i]
            bp.co = Vector((0.0, y, 0.0))
            bp.handle_left_type = bp.handle_right_type = "AUTO"
        obj = bpy.data.objects.new(curve.name, curve)
        if origin is not None:
            obj.matrix_world = origin
        else:
            obj.location = context.scene.cursor.location
        _tag(obj, shot, char, "path")
        _link(context, shot, obj)
        return {"FINISHED"}


class CHIMERA_OT_add_waypoint(_TargetOp, bpy.types.Operator):
    bl_idname = "chimera.add_waypoint"
    bl_label = "Waypoint"
    bl_description = "Sparse root target at the 3D cursor for the current frame"
    use_heading: BoolProperty(name="Constrain heading", default=False)

    def execute(self, context):
        shot = active_shot(context)
        char = _active_char(shot)
        f = _local_frame(context, shot)
        obj = bpy.data.objects.new(f"WP_{shot.name}_{char.char_id}_{f:04d}", None)
        obj.empty_display_type = "CONE"
        obj.empty_display_size = 0.3
        obj.location = context.scene.cursor.location
        _tag(obj, shot, char, "waypoint", f)
        obj["chimera_use_heading"] = self.use_heading
        _link(context, shot, obj)
        return {"FINISHED"}


class CHIMERA_OT_add_effector(_TargetOp, bpy.types.Operator):
    bl_idname = "chimera.add_effector"
    bl_label = "Effector Target"
    bl_description = "Hand/foot/head target at the 3D cursor for the current frame"
    joint: EnumProperty(name="Joint", items=constraints.EFFECTOR_JOINTS)

    def execute(self, context):
        shot = active_shot(context)
        char = _active_char(shot)
        f = _local_frame(context, shot)
        obj = bpy.data.objects.new(f"{self.joint.upper()}_{shot.name}_{char.char_id}_{f:04d}", None)
        obj.empty_display_type = "SPHERE"
        obj.empty_display_size = 0.08
        obj.location = context.scene.cursor.location
        _tag(obj, shot, char, "effector", f)
        obj["chimera_joint"] = self.joint
        _link(context, shot, obj)
        return {"FINISHED"}


classes = (CHIMERA_OT_add_start, CHIMERA_OT_add_path, CHIMERA_OT_add_waypoint, CHIMERA_OT_add_effector)
