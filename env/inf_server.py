#!/usr/bin/env python3
"""Inference-Server fuer den Live-Bot. Laedt den BC-Checkpoint (reloadt automatisch,
wenn das Training ihn aktualisiert) und liefert zu einer Beobachtung + Kontext die
konkrete Spiel-Aktion (Intent). Der headless Bot-Client (TS) schickt die encodierte
obs + ctx per POST /act und bekommt den Intent zum Senden an den Server zurueck.

  python env/inf_server.py [--ckpt checkpoints/bc_real.pt] [--port 8650] [--device cpu]
"""
from __future__ import annotations
import http.server, json, os, sys, threading, argparse
import numpy as np, torch

sys.path.insert(0, os.path.dirname(__file__))
import actions as AC
import featurize as F
from net import Net

CKPT = "checkpoints/bc_real.pt"; DEV = "cpu"
_net = None; _mtime = -1.0; _step = 0; _lock = threading.Lock()

def get_net():
    global _net, _mtime, _step
    with _lock:
        m = os.path.getmtime(CKPT) if os.path.exists(CKPT) else 0.0
        if _net is None or m > _mtime:
            n = Net().to(DEV)
            if os.path.exists(CKPT):
                c = torch.load(CKPT, map_location=DEV, weights_only=True)
                n.load_state_dict(c["model"]); _mtime = m; _step = c.get("gstep", 0)
            n.eval(); _net = n
    return _net

@torch.no_grad()
def act(o, sample=False, temp=1.0):
    net = get_net()
    # Rohe obs vom TS-Runner: map ist float-Kanalstapel (encodeMap) ODER map_u8
    # (uint8-quantisiert, ~3x kleinere Payload) -> dann dequantisieren.
    if "map_u8" in o:
        mp = torch.tensor((np.asarray(o["map_u8"], np.float32) / 127.5 - 1.0).reshape(1, 18, 90, 180))
    else:
        mp = torch.tensor(np.asarray(o["map"], np.float32).reshape(1, 18, 90, 180))
    own = torch.tensor(np.asarray(F.featurize_own(o["own"]), np.float32).reshape(1, -1))
    om, mk = F.featurize_opps(o.get("opps", []))
    opp = torch.tensor(np.asarray(om, np.float32).reshape(1, AC.MAX_OPP, -1))
    mask = torch.tensor(np.asarray(mk, bool).reshape(1, AC.MAX_OPP))
    cfg = torch.tensor(np.asarray(F.featurize_config(o.get("config", {})), np.float32).reshape(1, -1))
    out = net(mp, own, opp, mask, config_t=cfg)   # kein coarse_idx -> fine folgt vorhergesagter Grobzelle
    def pick(logits):
        if sample:
            p = torch.softmax(logits.flatten() / max(1e-3, temp), 0)
            return int(torch.multinomial(p, 1))
        return int(logits.argmax())
    at = pick(out["atype"])
    action = AC.Action(atype=at)
    for h in AC.HEAD_SCHEMA.get(AC.A(at), ()):
        setattr(action, h, pick(out[h]))
    c = o["ctx"]
    ctx = AC.Context(map_w=c["mapW"], map_h=c["mapH"], troops=c["troops"], gold=c["gold"],
                     opp_ids=c["oppIds"], own_unit_ids=c["ownUnitIds"], own_attack_ids=c["ownAttackIds"])
    return {"intent": AC.decode(action, ctx), "atype": AC.A(at).name, "value": float(out["value"])}

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self._cors(); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        get_net(); self._send(200, {"ok": True, "ckpt_step": _step})
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            o = json.loads(self.rfile.read(n))
            res = act(o, sample=o.get("sample", False), temp=o.get("temp", 1.0))
            src = self.client_address[0]
            if not src.startswith("127."):   # Browser (nicht die lokalen Watch-Container)
                print(f"[act] {src} -> {res['atype']}", flush=True)
            self._send(200, res)
        except Exception as e:
            self._send(500, {"error": str(e)})

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=CKPT); ap.add_argument("--port", type=int, default=8650)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args(); CKPT = a.ckpt; DEV = a.device
    print(f"inf_server auf {a.host}:{a.port}, ckpt={CKPT}, device={DEV}", flush=True)
    http.server.ThreadingHTTPServer((a.host, a.port), H).serve_forever()
