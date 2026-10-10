# SPDX-License-Identifier: GPL-3.0-or-later
"""Chimera Director: Blender front end for the text-to-motion -> video pipeline.

The add-on only does bpy work and HTTP. All inference, caching and GPU
scheduling live in the orchestrator (see docs/architecture.md).
"""

bl_info = {  # legacy (pre-4.2) installs; extensions use blender_manifest.toml
    "name": "Chimera Director",
    "author": "RPGrafx",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Director",
    "description": "Text-to-motion and video generation pipeline front end",
    "category": "Animation",
}

import bpy  # noqa: E402

from . import ops, prefs, props, sync, ui  # noqa: E402

_classes = (*prefs.classes, *props.classes, *ops.classes, *ui.classes)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    props.register()
    sync.register()


def unregister():
    sync.unregister()
    props.unregister()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
