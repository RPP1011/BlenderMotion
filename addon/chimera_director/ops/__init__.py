from . import motion, plan, project, render, rig_map, targets, ui_ops

classes = (
    *project.classes, *plan.classes, *motion.classes, *render.classes,
    *targets.classes, *rig_map.classes, *ui_ops.classes,
)
