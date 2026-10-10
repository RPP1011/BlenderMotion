"""Stage 4: control passes for one shot (architecture §4.3, VACE-free).

    blender -b <snapshot.blend> --python blender_worker/passes.py -- <spec.json>

The spec is the add-on's passes body (docs/addon.md) plus `out_dir`. Writes into
out_dir:

  kp2d.json      2D keypoints per frame, COCO-18 order, pixel coords + visibility
  first.png      beauty still of the in-frame   (H3 first/last-frame input)
  last.png       beauty still of the last frame
  playblast.mp4  frames_wan frames; the last pose is held past the out-point

Runs on a snapshot copy, never the user's file. Prints PASSES_OK on success.

Optional spec keys (defaults suit the workstation; tests override them):
  still_engine     "EEVEE" | "CYCLES" | "WORKBENCH"       (default EEVEE)
  playblast_engine "WORKBENCH" | "EEVEE" | "CYCLES"       (default WORKBENCH)
  resolution       [w, h]                                (default: scene's)
  resolution_scale percentage applied to both            (default 100)
  bonemaps         {char_id: {canonical_joint: bone}}    (default: name guess)
"""

import json
import os
import shutil
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view

# COCO-18 (OpenPose body-18) order. Face points have no rig bones; they are
# emitted as null and must be excluded from PCK.
COCO18 = [
    "nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow",
    "l_wrist", "r_hip", "r_knee", "r_ankle", "l_hip", "l_knee", "l_ankle",
    "r_eye", "l_eye", "r_ear", "l_ear",
]
# COCO joint -> (canonical joint, which end of its bone). Canonical names are
# the add-on's bone-map keys (ops/rig_map.py). Shoulder/elbow/wrist/hip/knee/
# ankle are the heads of the upper-arm/forearm/hand/thigh/shin/foot bones.
# OpenPose "neck" is the point between the shoulders, i.e. the neck bone's head.
COCO_FROM = {
    "neck": ("neck", "head"),
    "r_shoulder": ("r_shoulder", "head"), "r_elbow": ("r_elbow", "head"),
    "r_wrist": ("r_wrist", "head"),
    "l_shoulder": ("l_shoulder", "head"), "l_elbow": ("l_elbow", "head"),
    "l_wrist": ("l_wrist", "head"),
    "r_hip": ("r_hip", "head"), "r_knee": ("r_knee", "head"), "r_ankle": ("r_ankle", "head"),
    "l_hip": ("l_hip", "head"), "l_knee": ("l_knee", "head"), "l_ankle": ("l_ankle", "head"),
}
# A ray from the camera that hits the character's own skin this close in front
# of the joint is the skin over that joint, not an occluder (joints sit inside
# the mesh). Farther in front means another body part covers it.
SELF_MARGIN = 0.15

ENGINE_IDS = {
    "CYCLES": ["CYCLES"],
    "WORKBENCH": ["BLENDER_WORKBENCH"],
    "EEVEE": ["BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"],  # 4.2-4.x, 5.x
}


def log(msg):
    print(f"[passes] {msg}", flush=True)


# -- setup ----------------------------------------------------------------------

def guess_bonemap(rig):
    """Fallback when the add-on sent no bone map: reuse its name heuristics."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "addon",
                        "chimera_director", "bonemap.py")
    spec = importlib.util.spec_from_file_location("chimera_bonemap", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.guess([b.name for b in rig.data.bones])


def isolate_characters(scene, rigs):
    """Hide every armature (and what it deforms) that is not in this shot."""
    keep = set(rigs)
    for obj in scene.objects:
        if obj.type == "ARMATURE" and obj not in keep:
            for o in [obj, *obj.children_recursive]:
                o.hide_render = True
                o.hide_viewport = True


def own_meshes(rig):
    return {o for o in rig.children_recursive if o.type == "MESH"}


def set_engine(scene, name):
    # The engine enum is dynamic (Cycles registers itself), so try each id.
    for engine in ENGINE_IDS[name.upper()]:
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    else:
        raise RuntimeError(f"render engine {name} not available")
    if engine == "CYCLES":
        scene.cycles.device = "CPU"
        scene.cycles.samples = 16
        scene.cycles.use_denoising = False
    if engine == "BLENDER_WORKBENCH":
        shading = scene.display.shading
        shading.light = "STUDIO"
        shading.color_type = "MATERIAL"
    return engine


def set_image_output(scene, fmt):
    img = scene.render.image_settings
    if hasattr(img, "media_type"):  # 5.0+
        img.media_type = "VIDEO" if fmt == "FFMPEG" else "IMAGE"
    img.file_format = fmt
    if fmt == "PNG":
        img.color_mode = "RGB"


# -- keypoints ------------------------------------------------------------------

def joint_world(rig, bone_name, end):
    pb = rig.pose.bones.get(bone_name)
    if pb is None:
        return None
    return rig.matrix_world @ (pb.head if end == "head" else pb.tail)


def visible(scene, depsgraph, cam_pos, target, own):
    """True if nothing but the character's skin over the joint lies between."""
    direction = target - cam_pos
    dist = direction.length
    direction.normalize()
    origin = cam_pos.copy()
    travelled = 0.0
    for _ in range(16):
        hit, loc, _n, _i, obj, _m = scene.ray_cast(depsgraph, origin, direction,
                                                     distance=dist - travelled)
        if not hit:
            return True
        travelled = (loc - cam_pos).length
        if obj.original not in own:
            return False
        if dist - travelled > SELF_MARGIN:
            return False
        origin = loc + direction * 1e-4
    return True


def keypoints_for_frame(scene, depsgraph, cam, res, chars):
    cam_pos = cam.matrix_world.translation
    out = {}
    for cid, (rig, bmap, own) in chars.items():
        pts = []
        for name in COCO18:
            src = COCO_FROM.get(name)
            bone = bmap.get(src[0]) if src else None
            p = joint_world(rig, bone, src[1]) if bone else None
            if p is None:
                pts.append(None)
                continue
            ndc = world_to_camera_view(scene, cam, p)
            x, y = ndc.x * res[0], (1.0 - ndc.y) * res[1]
            inside = ndc.z > 0 and 0 <= ndc.x <= 1 and 0 <= ndc.y <= 1
            v = 1 if inside and visible(scene, depsgraph, cam_pos, p, own) else 0
            pts.append([round(x, 2), round(y, 2), v])
        out[cid] = pts
    return out


# -- rendering --------------------------------------------------------------------

def render_still(scene, path):
    set_image_output(scene, "PNG")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True, scene=scene.name)


def encode_sequence(src_scene, frame_paths, out_path):
    """PNG sequence -> H.264 mp4 through Blender's own ffmpeg, via a throwaway
    sequencer scene (no system ffmpeg needed on the workstation)."""
    seq_scene = bpy.data.scenes.new("_passes_encode")
    r = seq_scene.render
    r.resolution_x, r.resolution_y = src_scene.render.resolution_x, src_scene.render.resolution_y
    r.resolution_percentage = src_scene.render.resolution_percentage
    r.fps, r.fps_base = src_scene.render.fps, src_scene.render.fps_base
    seq_scene.frame_start, seq_scene.frame_end = 1, len(frame_paths)
    ed = seq_scene.sequence_editor_create()
    strips = ed.strips if hasattr(ed, "strips") else ed.sequences  # renamed in 4.4
    strip = strips.new_image("frames", frame_paths[0], channel=1, frame_start=1)
    for p in frame_paths[1:]:
        strip.elements.append(os.path.basename(p))
    r.use_sequencer = True
    set_image_output(seq_scene, "FFMPEG")
    r.ffmpeg.format = "MPEG4"
    r.ffmpeg.codec = "H264"
    r.ffmpeg.constant_rate_factor = "HIGH"
    r.filepath = out_path
    r.use_file_extension = False
    bpy.ops.render.render(animation=True, scene=seq_scene.name)
    bpy.data.scenes.remove(seq_scene)


def run(spec):
    out_dir = spec["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.data.scenes[spec["scene"]]
    cam = bpy.data.objects[spec["camera"]]
    scene.camera = cam
    r = scene.render
    r.use_sequencer = False
    r.use_compositing = False
    if spec.get("resolution"):
        r.resolution_x, r.resolution_y = spec["resolution"]
    r.resolution_percentage = int(spec.get("resolution_scale", 100))
    res = (r.resolution_x * r.resolution_percentage // 100,
           r.resolution_y * r.resolution_percentage // 100)

    rigs = {cid: bpy.data.objects[name] for cid, name in spec["characters"].items()}
    isolate_characters(scene, rigs.values())
    chars = {}
    for cid, rig in rigs.items():
        bmap = (spec.get("bonemaps") or {}).get(cid) or guess_bonemap(rig)
        chars[cid] = (rig, bmap, own_meshes(rig))

    f_in, f_out = int(spec["frame_in"]), int(spec["frame_out"])
    last = f_out - 1
    total = int(spec.get("frames_wan") or (f_out - f_in))
    # Frame i of the output shows scene frame min(f_in + i, last): hold, never trim.
    src_frames = [min(f_in + i, last) for i in range(total)]

    # kp2d: cheap, so per output frame (held frames repeat the last pose).
    depsgraph = bpy.context.evaluated_depsgraph_get()
    cache, frames = {}, []
    for i, f in enumerate(src_frames):
        if f not in cache:
            scene.frame_set(f)
            cache[f] = keypoints_for_frame(scene, depsgraph, cam, res, chars)
        frames.append({"i": i, "scene_frame": f, "people": cache[f]})
    kp = {"format": "coco18", "joints": COCO18, "resolution": list(res),
          "fps": r.fps / r.fps_base, "frame_in": f_in, "frame_out": f_out,
          "frames_held": total - (f_out - f_in), "frames": frames}
    with open(os.path.join(out_dir, "kp2d.json"), "w") as fh:
        json.dump(kp, fh)
    log(f"kp2d: {len(frames)} frames, {len(chars)} character(s)")

    # Beauty stills.
    engine = set_engine(scene, spec.get("still_engine", "EEVEE"))
    for name, f in (("first", f_in), ("last", last)):
        scene.frame_set(f)
        render_still(scene, os.path.join(out_dir, f"{name}.png"))
    log(f"stills: {engine}")

    # Playblast: render each distinct scene frame once, copy for held frames.
    engine = set_engine(scene, spec.get("playblast_engine", "WORKBENCH"))
    tmp = os.path.join(out_dir, "_frames")
    os.makedirs(tmp, exist_ok=True)
    paths, rendered = [], {}
    for i, f in enumerate(src_frames):
        path = os.path.join(tmp, f"{i:05d}.png")
        if f in rendered:
            shutil.copyfile(rendered[f], path)
        else:
            scene.frame_set(f)
            render_still(scene, path)
            rendered[f] = path
        paths.append(path)
    encode_sequence(scene, paths, os.path.join(out_dir, "playblast.mp4"))
    shutil.rmtree(tmp)
    log(f"playblast: {total} frames ({len(rendered)} rendered), {engine}")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    with open(argv[0]) as fh:
        spec = json.load(fh)
    run(spec)
    print("PASSES_OK", flush=True)


if __name__ == "__main__":
    main()
