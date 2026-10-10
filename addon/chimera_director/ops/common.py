
from .. import client as _client, contract, events, prefs


def project(context):
    return context.scene.chimera


def active_shot(context):
    return context.scene.chimera.active_shot


def get_client(timeout=5.0):
    return _client.Client(prefs.server_url(), timeout=timeout)


def push_shot(context, shot):
    p = project(context)
    return get_client().put_shot(p.project_name, shot.name, contract.shot_to_dict(shot, p))


def track_job(context, shot, stage, resp):
    job_id = resp.get("job_id")
    if not job_id:
        raise _client.OrchestratorError(f"no job_id in response: {resp}")
    st = shot.stage(stage)
    st.job_id = job_id
    st.status = resp.get("status", "queued")
    st.progress = 0.0
    st.message = ""
    events.WATCHER.watch(job_id, context.scene.name, shot.name, stage)
    return job_id


class ShotOperator:
    """Mixin: operator that needs an active shot."""

    @classmethod
    def poll(cls, context):
        return active_shot(context) is not None


def report_error(op, e):
    op.report({"ERROR"}, str(e))
    return {"CANCELLED"}

