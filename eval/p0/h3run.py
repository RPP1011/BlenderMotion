"""Queue one H3 generation on the local ComfyUI and wait for the mp4.

python h3run.py flf  <first.png> <last.png> <prompt> <out.mp4> [frames] [w h] [seed]
python h3run.py ref2v <ref_video.mp4> <prompt> <out.mp4> [frames] [w h] [seed] [ref_image.png]
"""
import json
import os
import shutil
import sys
import time
import urllib.request
import uuid

URL = "http://127.0.0.1:8188"
COMFY = "/workspace/runpod-slim/ComfyUI"


def post(path, data):
    req = urllib.request.Request(URL + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def get(path):
    return json.load(urllib.request.urlopen(URL + path))


def stage_input(path):
    name = f"p0_{uuid.uuid4().hex[:8]}_{os.path.basename(path)}"
    shutil.copyfile(path, os.path.join(COMFY, "input", name))
    return name


def common(unet, cond_node, frames, seed, steps=None):
    steps = steps or int(os.environ.get("H3_STEPS", "20"))
    g = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                                                        "type": "minimax", "device": "default"}},
        "vvae": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "avae": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "sched": {"class_type": "BasicScheduler", "inputs": {"model": ["unet", 0], "scheduler": "simple",
                                                             "steps": steps, "denoise": 1.0}},
        "guider": {"class_type": "BasicGuider", "inputs": {"model": ["unet", 0], "conditioning": [cond_node, 0]}},
        "sample": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
            "sigmas": ["sched", 0], "latent_image": [cond_node, 1]}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vvae", 0]}},
        "deca": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["sample", 0], "vae": ["avae", 0]}},
        "video": {"class_type": "CreateVideo", "inputs": {"images": ["dec", 0], "audio": ["deca", 0],
                                                          "fps": 24.0, "bit_depth": 8}},
        "save": {"class_type": "SaveVideo", "inputs": {"video": ["video", 0], "filename_prefix": "video/p0",
                                                       "format": "mp4", "codec": "auto"}},
    }
    lora = os.environ.get("H3_LORA")
    if lora:  # turbo LoRA: patch the model feeding the scheduler and guider
        g["lora"] = {"class_type": "LoraLoaderModelOnly",
                     "inputs": {"model": ["unet", 0], "lora_name": lora, "strength_model": 1.0}}
        g["sched"]["inputs"]["model"] = ["lora", 0]
        g["guider"]["inputs"]["model"] = ["lora", 0]
    return g


def main():
    a = sys.argv[1:]
    mode = a[0]
    if mode == "flf":
        first, last, prompt, out = a[1:5]
        rest = a[5:]
    else:
        ref, prompt, out = a[1:4]
        rest = a[4:]
    frames = int(rest[0]) if rest else 124
    w, h = (int(rest[1]), int(rest[2])) if len(rest) > 2 else (960, 544)
    seed = int(rest[3]) if len(rest) > 3 else 42
    if mode == "flf":
        g = common("minimax_h3_fl2va_pruned_int8_convrot.safetensors", "cond", frames, seed)
        g["first"] = {"class_type": "LoadImage", "inputs": {"image": stage_input(first)}}
        g["last"] = {"class_type": "LoadImage", "inputs": {"image": stage_input(last)}}
        g["cond"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["clip", 0], "vae": ["vvae", 0], "prompt": prompt, "width": w, "height": h,
            "length": frames, "first_frame": ["first", 0], "last_frame": ["last", 0]}}
    else:
        g = common("minimax_h3_ref2va_pruned_int8_convrot.safetensors", "cond", frames, seed)
        g["refv"] = {"class_type": "LoadVideo", "inputs": {"file": stage_input(ref)}}
        g["refc"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["refv", 0]}}
        inputs = {"clip": ["clip", 0], "vae": ["vvae", 0], "audio_vae": ["avae", 0], "prompt": prompt,
                  "width": w, "height": h, "length": frames, "ref_image_size": "match",
                  "ref_videos.ref_video_0": ["refc", 0]}
        for k, img in enumerate(rest[4:13]):  # up to 9 reference images, tagged Picture 1..N
            g[f"refi{k}"] = {"class_type": "LoadImage", "inputs": {"image": stage_input(img)}}
            inputs[f"ref_images.ref_image_{k}"] = [f"refi{k}", 0]
        g["cond"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": inputs}
    t0 = time.time()
    r = post("/prompt", {"prompt": g, "client_id": "p0"})
    pid = r["prompt_id"]
    while True:
        time.sleep(5)
        hist = get(f"/history/{pid}")
        if pid in hist:
            st = hist[pid]["status"]
            if st.get("status_str") == "error":
                print("H3_ERROR", json.dumps(st)[:3000])
                sys.exit(1)
            if st.get("completed"):
                break
    outs = hist[pid]["outputs"]["save"]
    item = (outs.get("images") or outs.get("videos") or outs.get("gifs"))[0]
    src = os.path.join(COMFY, "output", item.get("subfolder", ""), item["filename"])
    shutil.copyfile(src, out)
    print(f"H3_OK {out} {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
