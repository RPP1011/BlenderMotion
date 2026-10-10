"""Viewport and timeline overlays (gpu + blf draw handlers).

Viewport: the active shot's targets get labels and a per-second ruler along
the root path, and a HUD in the corner shows the shot, its action and the
pipeline state. Timeline / Dope Sheet / NLA: every shot is drawn as a band
between its markers, coloured by state, with the active shot outlined.
"""

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .. import constraints, scene_io
from . import state

_handles = []
_path_cache = {}


def _scale():
    return bpy.context.preferences.system.ui_scale


def _shader_flat():
    return gpu.shader.from_builtin("UNIFORM_COLOR")


def _rect(x0, y0, x1, y1, color):
    sh = _shader_flat()
    batch = batch_for_shader(sh, "TRIS", {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]},
                             indices=[(0, 1, 2), (0, 2, 3)])
    sh.uniform_float("color", color)
    batch.draw(sh)


def _outline(x0, y0, x1, y1, color):
    sh = _shader_flat()
    batch = batch_for_shader(sh, "LINE_LOOP", {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]})
    sh.uniform_float("color", color)
    batch.draw(sh)


def _text(x, y, s, size=11, color=(1, 1, 1, 0.95), shadow=True):
    fid = 0
    blf.size(fid, size * _scale())
    if shadow:
        blf.enable(fid, blf.SHADOW)
        blf.shadow(fid, 3, 0.0, 0.0, 0.0, 0.8)
        blf.shadow_offset(fid, 1, -1)
    blf.position(fid, x, y, 0)
    blf.color(fid, *color)
    blf.draw(fid, s)
    if shadow:
        blf.disable(fid, blf.SHADOW)
    return blf.dimensions(fid, s)


def _lines3d(points, color, width=2.0, mode="LINE_STRIP"):
    if len(points) < 2:
        return
    sh = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    region = bpy.context.region
    sh.uniform_float("viewportSize", (region.width, region.height))
    sh.uniform_float("lineWidth", width * _scale())
    sh.uniform_float("color", color)
    batch_for_shader(sh, mode, {"pos": points}).draw(sh)


def _project():
    scene = bpy.context.scene
    p = getattr(scene, "chimera", None)
    return (scene, p) if p is not None and len(p.shots) else (scene, None)


# -- viewport ------------------------------------------------------------------

def _path_points(obj, n):
    key = (obj.name, n, tuple(round(v, 4) for row in obj.matrix_world for v in row),
           tuple(tuple(round(c, 4) for c in bp.co) for sp in obj.data.splines for bp in sp.bezier_points))
    pts = _path_cache.get(key)
    if pts is None:
        if len(_path_cache) > 64:
            _path_cache.clear()
        pts = _path_cache[key] = constraints.sample_curve(obj, n)
    return pts


def draw_view3d_world():
    scene, p = _project()
    if p is None or not p.show_overlay:
        return
    shot = p.active_shot
    if shot is None:
        return
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    accent = (0.35, 0.75, 1.0, 0.9)
    for start in constraints.helpers(shot.name, role="start"):
        m = start.matrix_world
        o = m.translation
        fwd = (m.to_3x3() @ Vector((0, 1, 0))).normalized()
        side = (m.to_3x3() @ Vector((1, 0, 0))).normalized()
        tip = o + fwd * 0.9
        _lines3d([o, tip, tip, tip - fwd * 0.25 + side * 0.18, tip, tip - fwd * 0.25 - side * 0.18],
                 accent, 3.0, "LINES")
    n = scene_io.frames_for(shot, p.fps)
    for path in constraints.helpers(shot.name, role="path"):
        if path.type != "CURVE":
            continue
        pts = _path_points(path, n)
        ticks = []
        for i in range(0, len(pts), max(1, p.fps)):
            a = pts[i]
            b = pts[min(i + 1, len(pts) - 1)] if i + 1 < len(pts) else pts[i]
            d = (b - a)
            d = Vector((-d.y, d.x, 0)).normalized() * 0.12 if d.length > 1e-6 else Vector((0.12, 0, 0))
            ticks += [a - d, a + d]
        _lines3d(ticks, accent, 2.0, "LINES")
    gpu.state.blend_set("NONE")


def draw_view3d_pixel():
    scene, p = _project()
    if p is None or not p.show_overlay:
        return
    ctx = bpy.context
    region, rv3d = ctx.region, ctx.region_data
    shot = p.active_shot
    if shot is None:
        return
    s = _scale()

    def label(co, text, color=(0.75, 0.9, 1.0, 1.0)):
        xy = location_3d_to_region_2d(region, rv3d, co)
        if xy is not None:
            _text(xy.x + 8 * s, xy.y + 6 * s, text, 11, color)

    for obj in constraints.helpers(shot.name):
        role = obj.get("chimera_role")
        char = obj.get("chimera_char", "")
        co = obj.matrix_world.translation
        if role == "start":
            label(co, f"START · {char}")
        elif role == "waypoint":
            label(co, f"{char} @ f{obj.get('chimera_frame', 0)}")
        elif role == "effector":
            label(co, f"{obj.get('chimera_joint', '')} @ f{obj.get('chimera_frame', 0)}", (1.0, 0.85, 0.5, 1.0))
        elif role == "path" and obj.type == "CURVE":
            pts = _path_points(obj, scene_io.frames_for(shot, p.fps))
            for i in range(p.fps, len(pts), p.fps):
                label(pts[i], f"{i // p.fps}s", (0.75, 0.9, 1.0, 0.8))
    if _hud_here(ctx):
        _draw_hud(scene, p, shot, region)


def _hud_here(ctx):
    """HUD goes in camera views; only if none is visible, in every viewport."""
    if ctx.region_data.view_perspective == "CAMERA":
        return True
    for area in ctx.screen.areas:
        if area.type == "VIEW_3D":
            r3d = area.spaces.active.region_3d
            if r3d is not None and r3d.view_perspective == "CAMERA":
                return False
    return True


def _draw_hud(scene, p, shot, region):
    s = _scale()
    x = 14 * s
    for r in bpy.context.area.regions:
        if r.type == "TOOLS" and r.width > 1:
            x += r.width
    y = 16 * s
    rng = scene_io.shot_range(scene, shot.name)
    inside = rng is not None and rng[0] <= scene.frame_current < rng[1]

    # Stage chips, bottom line.
    cx = x
    for key, lbl, _icon in state.PIPELINE:
        st = state.status(shot, key)
        col = state.COLORS[st]
        w = 58 * s
        _rect(cx, y, cx + w, y + 16 * s, (col[0], col[1], col[2], 0.85 if st != "none" else 0.35))
        text = lbl
        stg = shot.stages.get(key)
        if stg and st in ("running", "queued", "waiting_gpu"):
            text = f"{lbl} {int(stg.progress * 100)}%"
        _text(cx + 6 * s, y + 4 * s, text, 10, shadow=False)
        cx += w + 4 * s
    if shot.pck >= 0:
        _text(cx + 6 * s, y + 4 * s, f"PCK {shot.pck:.2f}", 10)

    # Action prompt, wrapped, above.
    lines = state.wrap(shot.action_prompt or "(no action prompt)", min(region.width - x - 40 * s, 520 * s), 6.5 * s)
    yy = y + 26 * s
    for line in reversed(lines[:3]):
        _text(x, yy, line, 11, (0.9, 0.9, 0.9, 0.9))
        yy += 15 * s
    title = f"{shot.name.upper()}  ·  {shot.duration_s:.1f}s"
    if shot.characters:
        title += "  ·  " + ", ".join(c.char_id for c in shot.characters)
    if not inside:
        title += "   (playhead outside this shot)"
    _text(x, yy + 2 * s, title, 13, (1, 1, 1, 1) if inside else (1, 0.8, 0.5, 1))


# -- timeline / NLA ------------------------------------------------------------

def draw_timeline():
    scene, p = _project()
    if p is None or not p.show_overlay:
        return
    region = bpy.context.region
    v2d = region.view2d
    s = _scale()
    h = 16 * s
    # Just under the frame ruler, clear of the marker row at the bottom.
    y0 = max(2.0, region.height - 26 * s - h)
    gpu.state.blend_set("ALPHA")
    active = p.active_shot
    for shot in p.shots:
        rng = scene_io.shot_range(scene, shot.name)
        if rng is None:
            continue
        x0 = v2d.view_to_region(rng[0], 0, clip=False)[0]
        x1 = v2d.view_to_region(rng[1], 0, clip=False)[0]
        if x1 < 0 or x0 > region.width:
            continue
        col = state.COLORS[state.overall(shot)]
        is_active = shot == active
        _rect(x0 + 1, y0, x1 - 1, y0 + h, (col[0], col[1], col[2], 0.75 if is_active else 0.45))
        if is_active:
            _outline(x0 + 1, y0, x1 - 1, y0 + h, (1, 1, 1, 0.9))
        job = state.active_job(shot)
        if job:
            _rect(x0 + 1, y0, x0 + 1 + (x1 - x0 - 2) * job[2].progress, y0 + 3 * s, (1, 1, 1, 0.8))
        label = shot.name
        if x1 - x0 > 160 * s and shot.action_prompt:
            label += "  " + shot.action_prompt
        blf.size(0, 10 * s)
        while label and blf.dimensions(0, label)[0] > (x1 - x0) - 10 * s:
            label = label[:-2]
        if label:
            _text(max(x0, 0) + 5 * s, y0 + 4 * s, label, 10, shadow=False)
    gpu.state.blend_set("NONE")


def register():
    _handles.append((bpy.types.SpaceView3D, bpy.types.SpaceView3D.draw_handler_add(
        draw_view3d_world, (), "WINDOW", "POST_VIEW")))
    _handles.append((bpy.types.SpaceView3D, bpy.types.SpaceView3D.draw_handler_add(
        draw_view3d_pixel, (), "WINDOW", "POST_PIXEL")))
    for space in (bpy.types.SpaceDopeSheetEditor, bpy.types.SpaceNLA):
        _handles.append((space, space.draw_handler_add(draw_timeline, (), "WINDOW", "POST_PIXEL")))


def unregister():
    for space, h in _handles:
        space.draw_handler_remove(h, "WINDOW")
    _handles.clear()
    _path_cache.clear()
