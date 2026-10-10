"""Backend interface. Each backend is configured from its config section and
reports a one-line `health()` string (cached by the server, never slow on GET)."""

import os
import shutil
import subprocess


class Backend:
    kind = "base"
    impl = "base"

    def __init__(self, cfg, app):
        self.cfg = cfg          # this backend's config section
        self.app = app          # the Orchestrator (full config, gpus, paths)

    @property
    def device(self):
        return self.cfg.get("device")

    def health(self):
        return "ok"

    def describe(self):
        return f"{self.impl}"


def which(exe):
    """Absolute path of an executable (path or PATH lookup), or None."""
    if not exe:
        return None
    exe = os.path.expanduser(exe)
    if os.sep in exe:
        return exe if os.path.isfile(exe) and os.access(exe, os.X_OK) else None
    return shutil.which(exe)


_VERSIONS = {}


def blender_version(exe):
    """'Blender 4.2.3 LTS' or None; cached per executable."""
    path = which(exe)
    if not path:
        return None
    if path not in _VERSIONS:
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60).stdout
            _VERSIONS[path] = (out.splitlines() or ["Blender ?"])[0].strip()
        except (OSError, subprocess.SubprocessError):
            _VERSIONS[path] = None
    return _VERSIONS[path]
