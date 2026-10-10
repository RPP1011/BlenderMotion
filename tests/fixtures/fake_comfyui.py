"""A tiny fake ComfyUI (tests only): the endpoints the H3 backend uses.

/system_stats, /object_info/<node>, /upload/image (multipart), /prompt,
/history/<id>, /view, /interrupt, /queue and /ws (progress messages over a
real WebSocket handshake). A prompt "renders" for `delay` seconds.
"""

import base64
import hashlib
import json
import re
import struct
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FakeComfy:
    def __init__(self, delay=0.6):
        self.delay = delay
        self.uploads = {}
        self.prompts = {}       # pid -> {graph, client_id, t0, interrupted}
        self.interrupts = 0
        self.lock = threading.Lock()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def state(self, pid):
        p = self.prompts[pid]
        if p["interrupted"]:
            return "error"
        return "success" if time.time() - p["t0"] >= self.delay else "running"

    def _handler(self):
        fc = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, code, data):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _read(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/system_stats":
                    return self._json(200, {"system": {"comfyui_version": "0.0-fake"},
                                            "devices": [{"name": "Fake GPU", "vram_free": 20 * 2**30}]})
                if u.path.startswith("/object_info/"):
                    return self._json(200, {u.path.rsplit("/", 1)[1]: {}})
                if u.path.startswith("/history/"):
                    pid = u.path.rsplit("/", 1)[1]
                    if pid not in fc.prompts:
                        return self._json(200, {})
                    st = fc.state(pid)
                    if st == "running":
                        return self._json(200, {})
                    if st == "error":
                        return self._json(200, {pid: {"status": {"status_str": "error", "completed": False,
                                                                 "messages": [["execution_interrupted", {}]]},
                                                      "outputs": {}}})
                    return self._json(200, {pid: {"status": {"status_str": "success", "completed": True},
                                                  "outputs": {"save": {"images": [
                                                      {"filename": f"{pid}.mp4", "subfolder": "chimera",
                                                       "type": "output"}]}}}})
                if u.path == "/view":
                    q = urllib.parse.parse_qs(u.query)
                    raw = ("FAKEVIDEO " + q["filename"][0]).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if u.path == "/ws":
                    return self._ws(urllib.parse.parse_qs(u.query).get("clientId", [""])[0])
                return self._json(404, {"error": "nf"})

            def _ws(self, client_id):
                key = self.headers.get("Sec-WebSocket-Key", "")
                acc = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", acc)
                self.end_headers()
                self.close_connection = True

                def send(obj):
                    data = json.dumps(obj).encode()
                    hdr = bytes([0x81, len(data)]) if len(data) < 126 else \
                        bytes([0x81, 126]) + struct.pack(">H", len(data))
                    self.wfile.write(hdr + data)
                    self.wfile.flush()
                try:
                    t_end = time.time() + 30
                    while time.time() < t_end:
                        mine = [(pid, p) for pid, p in fc.prompts.items() if p["client_id"] == client_id]
                        if mine:
                            pid, p = mine[-1]
                            send({"type": "executing", "data": {"node": "unet", "prompt_id": pid}})
                            for i in range(1, 9):
                                send({"type": "progress", "data": {"value": i, "max": 8, "prompt_id": pid}})
                                time.sleep(fc.delay / 10)
                            send({"type": "executing", "data": {"node": None, "prompt_id": pid}})
                            return
                        time.sleep(0.02)
                except OSError:
                    return

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                raw = self._read()
                if u.path == "/upload/image":
                    m = re.search(rb'name="image"; filename="([^"]+)"\r\n[^\r]*\r\n\r\n', raw)
                    if not m:
                        return self._json(400, {"error": "no image part"})
                    name = m.group(1).decode()
                    body = raw[m.end():]
                    body = body[:body.rfind(b"\r\n--")]
                    fc.uploads[name] = body
                    return self._json(200, {"name": name, "subfolder": "", "type": "input"})
                if u.path == "/prompt":
                    d = json.loads(raw)
                    errors = {}
                    for nid, node in d["prompt"].items():
                        for k in ("image", "file"):
                            v = node["inputs"].get(k)
                            if node["class_type"] in ("LoadImage", "LoadVideo") and k in node["inputs"] \
                                    and v not in fc.uploads:
                                errors[nid] = f"missing input {v}"
                    if errors:
                        return self._json(200, {"prompt_id": "x", "node_errors": errors})
                    pid = uuid.uuid4().hex
                    fc.prompts[pid] = {"graph": d["prompt"], "client_id": d.get("client_id"), "t0": time.time(),
                                       "interrupted": False}
                    return self._json(200, {"prompt_id": pid, "number": len(fc.prompts), "node_errors": {}})
                if u.path in ("/interrupt", "/queue"):
                    fc.interrupts += 1
                    d = json.loads(raw or b"{}")
                    for pid in (d.get("delete") or []) + ([d["prompt_id"]] if d.get("prompt_id") else []):
                        if pid in fc.prompts:
                            fc.prompts[pid]["interrupted"] = True
                    return self._json(200, {})
                return self._json(404, {"error": "nf"})

        return H
