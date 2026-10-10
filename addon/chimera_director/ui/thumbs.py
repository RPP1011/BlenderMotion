"""Shot card thumbnails (first frame from the passes) via bpy.utils.previews."""

import os

import bpy.utils.previews

_coll = None


def icon_for(path):
    """icon_id for an image path, or 0. Reloads when the file changes."""
    global _coll
    if not path or not os.path.isfile(path):
        return 0
    if _coll is None:
        _coll = bpy.utils.previews.new()
    key = f"{path}:{os.path.getmtime(path)}"
    item = _coll.get(key)
    if item is None:
        item = _coll.load(key, path, "IMAGE")
    return item.icon_id


def unregister():
    global _coll
    if _coll is not None:
        bpy.utils.previews.remove(_coll)
        _coll = None
