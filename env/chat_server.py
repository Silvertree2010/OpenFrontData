#!/usr/bin/env python3
"""Winziger In-Memory-Chat fuer den oeffentlichen Watch-Client. Zweitrangig:
keine DB, keine Auth, Nachrichten leben nur im RAM (Ringpuffer). Wird lokal auf
dem Mac gehostet und via tailscale serve/funnel unter /chat oeffentlich gemacht.

  GET  /chat?since=<id>  -> {"messages":[{"id","name","text","t"}], "last": <id>}
  POST /chat  {"name","text"}  -> {"ok": true, "id": <id>}

Routing ist praefix-tolerant (matcht auf '/chat' irgendwo im Pfad), damit es egal
ist, ob der Reverse-Proxy das Prefix strippt.
"""
from __future__ import annotations
import http.server, json, threading, time, argparse
from urllib.parse import urlparse, parse_qs

MAX_MSGS = 300
MAX_NAME = 24
MAX_TEXT = 280

_lock = threading.Lock()
_msgs: list[dict] = []
_next_id = 1


def add_msg(name: str, text: str) -> int:
    global _next_id
    name = (name or "guest").strip()[:MAX_NAME] or "guest"
    text = (text or "").strip()[:MAX_TEXT]
    if not text:
        return -1
    with _lock:
        mid = _next_id
        _next_id += 1
        _msgs.append({"id": mid, "name": name, "text": text, "t": int(time.time())})
        if len(_msgs) > MAX_MSGS:
            del _msgs[: len(_msgs) - MAX_MSGS]
        return mid


def since(after: int) -> dict:
    with _lock:
        out = [m for m in _msgs if m["id"] > after]
        last = _msgs[-1]["id"] if _msgs else 0
    return {"messages": out, "last": last}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if "/chat" not in self.path:
            return self._send(404, {"error": "not found"})
        q = parse_qs(urlparse(self.path).query)
        after = int((q.get("since", ["0"])[0]) or 0)
        self._send(200, since(after))

    def do_POST(self):
        if "/chat" not in self.path:
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            o = json.loads(self.rfile.read(n) or b"{}")
            mid = add_msg(o.get("name", ""), o.get("text", ""))
            self._send(200, {"ok": mid > 0, "id": mid})
        except Exception as e:
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8651)
    a = ap.parse_args()
    print(f"chat_server auf {a.host}:{a.port}", flush=True)
    http.server.ThreadingHTTPServer((a.host, a.port), H).serve_forever()
