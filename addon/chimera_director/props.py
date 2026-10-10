"""PropertyGroups mirroring the shot JSON contract (docs §3.1).

The Blender-side copy is a working copy: it is pushed to the orchestrator
(PUT shot) before every job, and fields the add-on does not model are kept
verbatim in `extra_json` so a round trip never drops data.
"""

import bpy
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
    PointerProperty, StringProperty,
)

STAGES = ("motion", "retarget", "passes", "generate", "eval")

STATUS_ITEMS = [
    ("none", "Not run", "", "RADIOBUT_OFF", 0),
    ("queued", "Queued", "", "TIME", 1),
    ("waiting_gpu", "Waiting for GPU", "", "SORTTIME", 2),
    ("running", "Running", "", "PLAY", 3),
    ("done", "Done", "", "CHECKMARK", 4),
    ("failed", "Failed", "", "ERROR", 5),
    ("cancelled", "Cancelled", "", "CANCEL", 6),
    ("stale", "Stale", "Inputs changed since this ran", "FILE_REFRESH", 7),
]
ACTIVE_STATUSES = {"queued", "waiting_gpu", "running"}


class CHIMERA_PG_ref(bpy.types.PropertyGroup):
    path: StringProperty(name="Path", subtype="FILE_PATH")


class CHIMERA_PG_character(bpy.types.PropertyGroup):
    char_id: StringProperty(name="ID", default="hero")
    rig: StringProperty(
        name="Rig", description="Library reference '<file.blend>#<Object>' relative to the project root",
    )
    rig_object: PointerProperty(
        name="Rig object", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "ARMATURE",
        description="Armature in this scene that receives the motion (usually the library override)",
    )
    lora: StringProperty(name="LoRA")
    refs: CollectionProperty(type=CHIMERA_PG_ref)


class CHIMERA_PG_stage(bpy.types.PropertyGroup):
    # `name` (built in) holds the stage key.
    status: EnumProperty(items=STATUS_ITEMS, default="none")
    job_id: StringProperty()
    progress: FloatProperty(min=0.0, max=1.0, subtype="FACTOR")
    message: StringProperty()
    input_hash: StringProperty(description="Scene hash the stage last ran on")


class CHIMERA_PG_shot(bpy.types.PropertyGroup):
    # `name` (built in) holds the shot id, e.g. "sh_010".
    order: IntProperty(name="Order", default=10)
    duration_s: FloatProperty(name="Duration (s)", default=4.0, min=0.1, soft_max=60.0)
    action_prompt: StringProperty(name="Action")
    characters: CollectionProperty(type=CHIMERA_PG_character)
    active_character_index: IntProperty()

    motion_backend: EnumProperty(
        name="Backend", items=[("kimodo", "Kimodo", ""), ("hy_motion", "HY-Motion", "")],
    )
    motion_seed: IntProperty(name="Seed", default=1234, min=0)
    motion_variants: IntProperty(name="Variants", default=4, min=1, max=16)
    selected_variant: IntProperty(name="Selected variant", default=-1, min=-1)
    motion_source: EnumProperty(
        name="Source", items=[("generated", "Generated", ""), ("manual", "Manual", "Edited in Blender")],
    )
    continuity: BoolProperty(
        name="Continue from previous shot",
        description="Constrain frame 0 to the previous shot's last pose",
    )

    camera_mode: EnumProperty(
        name="Camera", items=[("authored", "Authored", ""), ("template", "Template", "")],
    )
    camera_template: StringProperty(name="Template", default="dolly_in_35mm")

    look_prompt: StringProperty(name="Look")

    render_path: EnumProperty(
        name="Render path",
        items=[("A", "A: VACE", "Pose/depth-controlled VACE"), ("B", "B: H3", "MiniMax H3")],
    )
    ctrl_pose: BoolProperty(name="Pose", default=True)
    ctrl_depth: BoolProperty(name="Depth", default=True)
    ctrl_line: BoolProperty(name="Line art", default=False)
    steps: IntProperty(name="Steps", default=30, min=1, max=200)
    h3_mode: EnumProperty(
        name="H3 mode",
        items=[("flf", "First/last frame", ""), ("ref2v", "Reference video", "")],
    )
    audio_prompt: StringProperty(name="Audio")
    resolution_x: IntProperty(name="Width", default=1280, min=64)
    resolution_y: IntProperty(name="Height", default=720, min=64)

    stages: CollectionProperty(type=CHIMERA_PG_stage)
    scene_hash: StringProperty(description="Current hash of the shot's scene content")
    constraints_hash: StringProperty(description="Current hash of the shot's spatial targets")
    pck: FloatProperty(name="PCK@0.1", default=-1.0)
    identity_score: IntProperty(name="Identity (1-5)", default=0, min=0, max=5)
    extra_json: StringProperty(description="Unmodelled contract fields, preserved verbatim")

    def stage(self, key):
        st = self.stages.get(key)
        if st is None:
            st = self.stages.add()
            st.name = key
        return st


class CHIMERA_PG_bonemap_item(bpy.types.PropertyGroup):
    # `name` holds the canonical joint name.
    bone: StringProperty(name="Rig bone")


class CHIMERA_PG_project(bpy.types.PropertyGroup):
    project_name: StringProperty(name="Project", default="demo")
    root: StringProperty(name="Root", subtype="DIR_PATH", description="Project root directory")
    brief: PointerProperty(name="Brief", type=bpy.types.Text, description="Text datablock holding the brief")
    look_prompt: StringProperty(name="Global look")
    fps: IntProperty(name="FPS", default=24, min=1)
    resolution_x: IntProperty(name="Width", default=1280, min=64)
    resolution_y: IntProperty(name="Height", default=720, min=64)
    start_frame: IntProperty(name="Timeline start", default=1)

    shots: CollectionProperty(type=CHIMERA_PG_shot)
    active_shot_index: IntProperty(name="Active shot")

    server_status: StringProperty(default="unknown")
    server_detail: StringProperty()

    bonemap_rig: PointerProperty(
        name="Rig", type=bpy.types.Object, poll=lambda self, obj: obj.type == "ARMATURE",
    )
    bonemap: CollectionProperty(type=CHIMERA_PG_bonemap_item)
    bonemap_index: IntProperty()

    @property
    def active_shot(self):
        if 0 <= self.active_shot_index < len(self.shots):
            return self.shots[self.active_shot_index]
        return None

    def find_shot(self, shot_id):
        return self.shots.get(shot_id)


classes = (
    CHIMERA_PG_ref,
    CHIMERA_PG_character,
    CHIMERA_PG_stage,
    CHIMERA_PG_shot,
    CHIMERA_PG_bonemap_item,
    CHIMERA_PG_project,
)


def register():
    bpy.types.Scene.chimera = PointerProperty(type=CHIMERA_PG_project)


def unregister():
    del bpy.types.Scene.chimera
