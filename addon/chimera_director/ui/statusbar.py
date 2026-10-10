"""Running-job readout in Blender's status bar."""

import bpy

from . import state


def draw(self, context):
    p = getattr(context.scene, "chimera", None)
    if p is None or not len(p.shots):
        return
    jobs = []
    for shot in p.shots:
        job = state.active_job(shot)
        if job:
            jobs.append((shot, job))
    failed = [s for s in p.shots if state.overall(s) == "failed"]
    if not jobs and not failed and p.server_status == "ok":
        return
    row = self.layout.row(align=True)
    if p.server_status != "ok":
        row.label(text="Orchestrator offline", icon="UNLINKED")
    for shot, (_key, label, st) in jobs[:3]:
        row.label(text=f"{shot.name} {label} {int(st.progress * 100)}%", icon="SORTTIME")
    if len(jobs) > 3:
        row.label(text=f"+{len(jobs) - 3} more")
    if failed:
        row.label(text=f"{len(failed)} failed", icon="ERROR")


def register():
    bpy.types.STATUSBAR_HT_header.append(draw)


def unregister():
    bpy.types.STATUSBAR_HT_header.remove(draw)
