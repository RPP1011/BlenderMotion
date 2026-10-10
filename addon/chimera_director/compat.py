"""Small shims over API differences between Blender 4.2 and 5.x."""


def iter_fcurves(action):
    """Yield every F-curve of an action (legacy or layered/slotted)."""
    if action is None:
        return
    layers = getattr(action, "layers", None)
    if layers:
        for layer in layers:
            for strip in layer.strips:
                for bag in getattr(strip, "channelbags", ()):
                    yield from bag.fcurves
        return
    fcurves = getattr(action, "fcurves", None)  # removed in 5.0
    if fcurves is not None:
        yield from fcurves


def vse_strips(sequence_editor):
    """Top-level strip collection (`sequences` was renamed `strips` in 4.4)."""
    strips = getattr(sequence_editor, "strips", None)
    return strips if strips is not None else sequence_editor.sequences


def set_ffmpeg_output(image_settings):
    if hasattr(image_settings, "media_type"):  # 5.0+
        image_settings.media_type = "VIDEO"
    image_settings.file_format = "FFMPEG"


def assign_strip_slot(strip):
    """Make sure an NLA strip on a slotted action uses an OBJECT slot (4.4+)."""
    action = strip.action
    if action is None or not hasattr(strip, "action_slot"):
        return
    if strip.action_slot is not None:
        return
    for slot in action.slots:
        if slot.target_id_type == "OBJECT":
            strip.action_slot = slot
            return

