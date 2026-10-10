from . import list_shots, overlay, panel_project, panel_shot, statusbar, thumbs

# Registration order is sidebar order: project, shots, the shot card, then the rarely used bone map.
classes = (*list_shots.classes, panel_project.CHIMERA_PT_project, panel_project.CHIMERA_PT_shots,
           *panel_shot.classes, panel_project.CHIMERA_PT_bonemap, panel_project.CHIMERA_PT_edit_scene)


def register():
    overlay.register()
    statusbar.register()


def unregister():
    statusbar.unregister()
    overlay.unregister()
    thumbs.unregister()
