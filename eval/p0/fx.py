# Add render FX to an existing shot .blend (EEVEE): volumetric haze, magenta/cyan neon lights,
# depth of field focused on character B, motion blur. blender -b in.blend --python fx.py -- out.blend
import sys

import bpy
out = sys.argv[sys.argv.index("--") + 1]
s = bpy.context.scene
w = s.world
nt = w.node_tree
vol = nt.nodes.new("ShaderNodeVolumeScatter")
vol.inputs["Density"].default_value = 0.035
nt.links.new(vol.outputs[0], nt.nodes["World Output"].inputs["Volume"])
nt.nodes["Background"].inputs["Strength"].default_value = 0.3
for o in [o for o in s.objects if o.type == "LIGHT"]:
    o.data.energy *= 0.25
for name, col, loc in (("NeonMagenta", (1.0, 0.1, 0.7), (-3.0, 2.5, 3.0)), ("NeonCyan", (0.1, 0.8, 1.0), (4.5, -2.0, 2.6))):
    L = bpy.data.lights.new(name, "AREA")
    L.color, L.energy, L.size = col, 2500, 1.5
    o = bpy.data.objects.new(name, L)
    o.location = loc
    s.collection.objects.link(o)
    c = o.constraints.new("TRACK_TO")
    c.target, c.track_axis, c.up_axis = s.objects["B"], "TRACK_NEGATIVE_Z", "UP_Y"
cam = s.camera
cam.data.dof.use_dof = True
cam.data.dof.aperture_fstop = 1.4
cam.data.dof.focus_object = s.objects["B"]
cam.data.dof.focus_subtarget = "Head" if hasattr(cam.data.dof, "focus_subtarget") else ""
s.render.use_motion_blur = True
bpy.ops.wm.save_as_mainfile(filepath=out)
print("FX_OK")
