"""Backend registry: config section name -> {impl name: class}."""

from . import blender, evaluate, generate, motion, plan

REGISTRY = {
    "plan": plan.IMPLS,
    "motion": motion.IMPLS,
    "retarget": blender.RETARGET_IMPLS,
    "passes": blender.PASSES_IMPLS,
    "generate": generate.IMPLS,
    "eval": evaluate.IMPLS,
}


def make(section, cfg, app):
    impl = cfg[section].get("backend")
    try:
        cls = REGISTRY[section][impl]
    except KeyError:
        raise SystemExit(f"unknown {section} backend {impl!r}; choose from {sorted(REGISTRY[section])}") from None
    return cls(cfg[section], app)
