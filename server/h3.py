"""MiniMax H3 ComfyUI graphs (ported from eval/p0/h3run.py), prompt and refs.

Pure functions: the ComfyUI backend uploads files and posts what these build.
"""

import os

H3_FPS = 24
H3_MAX_FRAMES = 362   # ~15 s; H3 is trained on 124-362 frames
MAX_REF_IMAGES = 9

MOTION_TAG = ("<Video 1> is the motion reference: the body movement, timing, position in frame and the "
              "camera must follow <Video 1> exactly.")


def snap_frames(n):
    """Smallest 17k+5 >= n (H3's frame grid at 24 fps). Same as the add-on's h3_frame_count."""
    n = int(n)
    return 5 if n <= 5 else n + (17 - (n - 5) % 17) % 17


def snap_resolution(w, h, multiple=16):
    """Round to the nearest multiple (H3 latents are 16 px; 0/None disables)."""
    if not multiple:
        return int(w), int(h)
    r = lambda v: max(multiple, int(round(v / multiple)) * multiple)  # noqa: E731
    return r(w), r(h)


def _resolve(path, root):
    if not path:
        return None
    path = os.path.expanduser(path)
    if path.startswith("//"):  # Blender-relative: relative to the project root
        path = path[2:]
    return path if os.path.isabs(path) else os.path.join(root or "", path)


def reference_images(shot, root, limit=MAX_REF_IMAGES):
    """Ordered [(path, label)] of reference images for Ref2V.

    Sources (all round-trip through the add-on unchanged):
      characters[].refs   per-character images; label = char_id
      look.style_refs     look/style images; label = "style"
    Paths may be absolute, project-root relative or Blender '//'-relative.
    Missing files are skipped. At most `limit` (H3 takes 9)."""
    out = []
    for c in shot.get("characters") or []:
        for p in c.get("refs") or []:
            out.append((_resolve(p, root), c.get("char_id", "character")))
    for p in (shot.get("look") or {}).get("style_refs") or []:
        out.append((_resolve(p, root), "style"))
    seen, res = set(), []
    for p, label in out:
        if p and p not in seen and os.path.isfile(p):
            seen.add(p)
            res.append((p, label))
    return res[:limit]


def build_prompt(shot, mode, ref_labels=(), project=None):
    """Text prompt. `render.prompt` in the shot JSON overrides the generated one."""
    render = shot.get("render") or {}
    if render.get("prompt"):
        return render["prompt"]
    look = (shot.get("look") or {}).get("prompt") or ((project or {}).get("look") or {}).get("prompt") or ""
    action = shot.get("action_prompt") or ""
    parts = [s.strip().rstrip(".") + "." for s in (action, look) if s and s.strip()]
    if mode == "ref2v":
        by_label = {}
        for i, label in enumerate(ref_labels, 1):
            by_label.setdefault(label, []).append(f"<Picture {i}>")
        for label, tags in by_label.items():
            who = "the look and style" if label == "style" else f"the character {label}"
            parts.append(f"{' '.join(tags)} show{'s' if len(tags) == 1 else ''} {who}.")
        parts.append(MOTION_TAG)
    audio = render.get("audio_prompt")
    if audio:
        parts.append(f"Audio: {audio}.")
    return " ".join(parts)


def _common(cfg, unet, lora, steps, seed, prefix):
    g = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": cfg["text_encoder"], "type": "minimax",
                                                        "device": "default"}},
        "vvae": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["video_vae"]}},
        "avae": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["audio_vae"]}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": cfg["sampler"]}},
        "sched": {"class_type": "BasicScheduler", "inputs": {"model": ["unet", 0], "scheduler": cfg["scheduler"],
                                                             "steps": int(steps), "denoise": 1.0}},
        "guider": {"class_type": "BasicGuider", "inputs": {"model": ["unet", 0], "conditioning": ["cond", 0]}},
        "sample": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
            "sigmas": ["sched", 0], "latent_image": ["cond", 1]}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vvae", 0]}},
        "deca": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["sample", 0], "vae": ["avae", 0]}},
        "video": {"class_type": "CreateVideo", "inputs": {"images": ["dec", 0], "audio": ["deca", 0],
                                                          "fps": float(H3_FPS), "bit_depth": 8}},
        "save": {"class_type": "SaveVideo", "inputs": {"video": ["video", 0], "filename_prefix": prefix,
                                                       "format": "mp4", "codec": "auto"}},
    }
    if lora:  # e.g. the 8-step turbo LoRA: patch the model feeding the scheduler and guider
        g["lora"] = {"class_type": "LoraLoaderModelOnly",
                     "inputs": {"model": ["unet", 0], "lora_name": lora, "strength_model": 1.0}}
        g["sched"]["inputs"]["model"] = ["lora", 0]
        g["guider"]["inputs"]["model"] = ["lora", 0]
    return g


def build_graph(cfg, mode, prompt, frames, width, height, seed, inputs, prefix="chimera/h3"):
    """ComfyUI API-format graph.

    cfg: the `generate` config section. inputs: uploaded ComfyUI input names,
    {"first", "last"} for flf, {"video", "images": [...]} for ref2v."""
    m = cfg[mode]
    g = _common(cfg, m["unet"], m.get("lora"), m.get("steps", 20), seed, prefix)
    if mode == "flf":
        g["first"] = {"class_type": "LoadImage", "inputs": {"image": inputs["first"]}}
        g["last"] = {"class_type": "LoadImage", "inputs": {"image": inputs["last"]}}
        g["cond"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["clip", 0], "vae": ["vvae", 0], "prompt": prompt, "width": width, "height": height,
            "length": frames, "first_frame": ["first", 0], "last_frame": ["last", 0]}}
    elif mode == "ref2v":
        g["refv"] = {"class_type": "LoadVideo", "inputs": {"file": inputs["video"]}}
        g["refc"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["refv", 0]}}
        cin = {"clip": ["clip", 0], "vae": ["vvae", 0], "audio_vae": ["avae", 0], "prompt": prompt,
               "width": width, "height": height, "length": frames,
               "ref_image_size": cfg.get("ref_image_size", "match"),
               # dot-namespaced: a bare name is silently ignored by the node
               "ref_videos.ref_video_0": ["refc", 0]}
        for k, name in enumerate((inputs.get("images") or [])[:MAX_REF_IMAGES]):
            g[f"refi{k}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
            cin[f"ref_images.ref_image_{k}"] = [f"refi{k}", 0]
        g["cond"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": cin}
    else:
        raise ValueError(f"unknown H3 mode {mode!r}")
    return g
