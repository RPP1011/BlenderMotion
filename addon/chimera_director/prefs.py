import bpy
from bpy.props import FloatProperty, StringProperty


class CHIMERA_AP_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    server_url: StringProperty(
        name="Orchestrator URL", default="http://127.0.0.1:8765",
        description="Base URL of the orchestrator daemon",
    )
    poll_interval: FloatProperty(
        name="Job poll interval (s)", default=0.5, min=0.1, max=10.0,
        description="How often the background thread polls running jobs",
    )
    soma_hip_height: FloatProperty(
        name="SOMA hip height (m)", default=0.95, min=0.1, max=3.0,
        description="Hip height of the canonical skeleton, used to scale constraints "
                    "authored on your rig into the motion model's space. UNVERIFIED default",
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "server_url")
        col.prop(self, "poll_interval")
        col.prop(self, "soma_hip_height")


def get():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def server_url():
    p = get()
    return p.server_url.rstrip("/") if p else "http://127.0.0.1:8765"


classes = (CHIMERA_AP_preferences,)
