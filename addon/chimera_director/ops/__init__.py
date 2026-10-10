from . import motion, plan, project, render, rig_map, targets

classes = (
    *project.classes, *plan.classes, *motion.classes, *render.classes,
    *targets.classes, *rig_map.classes,
)
