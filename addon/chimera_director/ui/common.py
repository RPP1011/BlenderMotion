from .. import props

STATUS_ICON = {i[0]: i[3] for i in props.STATUS_ITEMS}
STAGE_LABEL = {"motion": "Motion", "retarget": "Retarget", "passes": "Passes",
               "generate": "Video", "eval": "Eval"}


def stage_icon(shot, key):
    st = shot.stages.get(key)
    return STATUS_ICON[st.status if st else "none"]


class SidebarPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Director"
