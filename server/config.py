"""Orchestrator configuration: defaults <- config file (JSON or TOML) <- CLI flags."""

import copy
import json
import os

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8765,
    "data_dir": "orchestrator_data",   # job store + roots of projects the add-on sent no root for
    "blender": "blender",               # shared by retarget and passes unless they set their own
    "cache": True,                      # reuse a stage's artifact when its input hash is unchanged
    # Named GPUs. A backend's "device" names one of these: the job holds that
    # GPU's lock while the stage runs (one GPU stage per card at a time), and
    # subprocesses get the env below (CUDA_VISIBLE_DEVICES).
    "gpus": {
        "gpu0": {"env": {"CUDA_VISIBLE_DEVICES": "0"}},
        "gpu1": {"env": {"CUDA_VISIBLE_DEVICES": "1"}},
    },
    "plan": {
        "backend": "heuristic",         # heuristic | llm (local OpenAI-compatible server) | fake
        "url": "http://127.0.0.1:8080/v1/chat/completions",
        "model": "local",
        "max_shots": 8,
        "default_duration_s": 4.0,
        "timeout_s": 240,
    },
    "motion": {
        "backend": "kimodo",            # kimodo | fake
        "device": "gpu1",
        "venv": "",                     # Kimodo venv; <venv>/bin is put first on PATH
        "command": ["kimodo_gen"],      # resolved in <venv>/bin when relative
        "extra_args": [],
        "env": {},
        "cwd": "",
        "num_samples": 4,               # takes generated per round; the best `variants` are returned
        "max_rounds": 2,                # extra rounds (new seeds) when every take is rejected
        "keep_rejected": False,         # True: return the least-bad take instead of failing
        "ranking": {},                  # overrides for server/takes.py DEFAULTS
        "timeout_s": 1800,
        # fake backend only: sample indices to emit floating (to exercise ranking)
        "fake_floating": [],
        "fake_delay_s": 0.0,
    },
    "retarget": {
        "backend": "blender",           # blender | fake
        "blender": "",                  # default: top-level "blender"
        "timeout_s": 900,
    },
    "passes": {
        "backend": "blender",           # blender | fake
        "blender": "",
        "device": None,                 # set to a GPU name if EEVEE should not overlap H3/Kimodo
        "still_engine": "EEVEE",
        "playblast_engine": "WORKBENCH",
        "resolution_scale": 100,
        "timeout_s": 1800,
        "fake_delay_s": 0.0,
    },
    "generate": {
        "backend": "comfyui",           # comfyui | fake
        "device": "gpu0",
        "url": "http://127.0.0.1:8188",
        "comfy_root": "",               # set to copy inputs/outputs on disk instead of HTTP upload/view
        "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "video_vae": "minimax_h3_video_vae_fp16.safetensors",
        "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
        "sampler": "res_multistep",
        "scheduler": "simple",
        "ref2v": {"unet": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
                  "lora": "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
                  "steps": 8},
        "flf": {"unet": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "lora": None, "steps": 20},
        "ref_image_size": "match",
        "seed": 42,                     # shot render.seed overrides
        "resolution_multiple": 16,
        "max_frames": 362,
        "emit_audio": True,             # also return the mp4 as `audio` (AUD_ strip)
        "poll_s": 2.0,
        "websocket": True,              # step progress over ComfyUI's /ws; /history polling decides completion
        "timeout_s": 7200,
        "fake_delay_s": 0.0,
    },
    "eval": {
        "backend": "dwpose",            # dwpose | fake | none
        "python": "",                   # interpreter with rtmlib + opencv + onnxruntime
        "device": None,                 # GPU name if onnxruntime-gpu is used (rtmlib device "cuda")
        "rtmlib_device": "cpu",
        "timeout_s": 3600,
        "fake_value": None,             # fake: fixed PCK (None = deterministic pseudo-random)
    },
}


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    if path.endswith(".toml"):
        try:
            import tomllib
        except ImportError as e:  # Python 3.10
            raise SystemExit("TOML config needs Python 3.11+ (tomllib); use a .json config") from e
        return tomllib.loads(raw.decode())
    return json.loads(raw)


def set_path(cfg, dotted, value):
    """--set motion.num_samples=6 : value parsed as JSON when possible."""
    try:
        value = json.loads(value)
    except (ValueError, TypeError):
        pass
    d = cfg
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def build(path=None, overrides=None, sets=(), fake=False, backends=()):
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        cfg = deep_merge(cfg, load_file(path))
    if fake:
        for section in ("plan", "motion", "retarget", "passes", "generate", "eval"):
            cfg[section]["backend"] = "fake"
    for spec in backends:  # "motion=fake"
        name, _, kind = spec.partition("=")
        cfg[name]["backend"] = kind
    for k, v in (overrides or {}).items():
        if v is not None:
            cfg[k] = v
    for s in sets:
        k, _, v = s.partition("=")
        set_path(cfg, k, v)
    cfg["data_dir"] = os.path.abspath(os.path.expanduser(cfg["data_dir"]))
    return cfg


def blender_for(cfg, section):
    return cfg[section].get("blender") or cfg.get("blender") or "blender"
