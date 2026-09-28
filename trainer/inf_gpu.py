#!/usr/bin/env python3
"""Inferenz-Server für D1 auf der GPU mit Stapelbildung. Gleiches Anfrageformat wie inf_d0.py
(POST /act, GET /), gedacht für Partien mit vielen KIs (arena.ts --ki 125).

Warum: Ein inf_d0 auf der CPU rechnet eine Anfrage nach der anderen. Gemessen am 20.09. mit
echten Anfragen: 11,7 ms je Anfrage (4 Threads, Maschine frei), davon 7,5 ms Kodierer. Auf der
5080 kostet derselbe Kodierer im Stapel von 32 nur 0,23 ms je Anfrage (4335 Anfragen/s). Bei 125
KIs in einer Partie kommen die Anfragen eines Ticks gleichzeitig, das ist genau ein Stapel.

Wie: HTTP-Threads legen die Anfrage in eine Warteschlange und warten. Ein Stapler-Thread sammelt
bis zu --max-stapel Anfragen (höchstens --max-warte-ms nach der ersten), baut daraus EINEN Batch,
rechnet den Kodierer (Karte, Gegner, Einheiten, Kern) einmal für alle und lässt danach je
Anfrage die Kopf-Kaskade laufen (unverändert aus spielen.Entscheider, mit der vorab gerechneten
Kodierung). Ergebnis je Anfrage wie bei inf_d0; mit gekoppeltem Ziehen (wahl_schluessel) hängt es
nicht von der Reihenfolge im Stapel ab.

fp32, TF32 aus: Die Schwelle für P(handeln) ist auf fp32 kalibriert (kal_*.json).

Mehrere Instanzen (verschiedene --port) teilen sich die GPU; arena.ts verteilt die KIs einer
Partie reihum auf die Server in --inf. Grund für mehrere: die Kopf-Kaskade läuft in Python und
ist der Engpass, nicht die GPU.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import queue
import sys
import threading
import time
from concurrent.futures import Future

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import torch  # noqa: E402

import daten as D  # noqa: E402
import netze as N  # noqa: E402
import spielen as SP  # noqa: E402


def _scheibe(x, i: int, n: int):
    """Zeile i eines Stapels der Grösse n (Batch-Dimension vorn), als Sicht ohne Kopie."""
    if isinstance(x, torch.Tensor):
        return x[i:i + 1] if x.dim() > 0 and x.shape[0] == n else x
    if isinstance(x, dict):
        return {k: _scheibe(v, i, n) for k, v in x.items()}
    if isinstance(x, list) and len(x) == n:
        return [x[i]]
    return x


class StapelEntscheider(SP.Entscheider):
    def entscheide_stapel(self, os_: list[dict], schwelle: float, top: int = 5) -> list:
        """Ergebnis je Anfrage: dict oder die Exception dieser Anfrage."""
        proben, idx, aus = [], [], [None] * len(os_)
        for j, o in enumerate(os_):
            try:
                proben.append(SP.anfrage_zu_sample(o, self.rep, self.zusatz))
                idx.append(j)
            except Exception as e:                       # eine kaputte Anfrage stoppt den Stapel nicht
                aus[j] = e
        if not proben:
            return aus
        n = len(proben)
        with self.lock, torch.no_grad():
            net = self.ad.modul
            net.train()                                  # wie Entscheider._koepfe: BatchNorm bleibt im eval
            for mod in net.modules():
                if isinstance(mod, torch.nn.modules.batchnorm._BatchNorm):
                    mod.eval()
            b_all = D.auf_geraet(D.sammle(proben), self.dev)
            b_all["zeiger_voll"] = True
            with self._autocast():
                z_all = net.kodiere(b_all, self.ad._karte(b_all))
            for k, j in enumerate(idx):
                try:
                    b = _scheibe(b_all, k, n)
                    b["zeiger_voll"] = True
                    self._z_vor = _scheibe(z_all, k, n)
                    w = os_[j].get("wahl_schluessel")
                    self._schluessel = str(w) if w is not None else None
                    aus[j] = self._entscheide(os_[j], schwelle, {}, top, True,
                                              vor=(b, proben[k]["cell"] is not None))
                except Exception as e:
                    aus[j] = e
                finally:
                    self._z_vor = None
                    self._schluessel = None
        return aus


class Stapler:
    def __init__(self, ent: StapelEntscheider, schwelle: float, max_stapel: int, max_warte: float):
        self.ent, self.schwelle = ent, schwelle
        self.max_stapel, self.max_warte = max_stapel, max_warte
        self.q: queue.Queue = queue.Queue()
        self.n_anfragen = self.n_stapel = 0
        self.sek_rechnen = 0.0
        self.groessen: dict[int, int] = {}
        threading.Thread(target=self._lauf, daemon=True).start()

    def anfrage(self, o: dict) -> dict:
        f: Future = Future()
        self.q.put((o, f))
        r = f.result()
        if isinstance(r, Exception):
            raise r
        return r

    def _lauf(self):
        while True:
            stapel = [self.q.get()]
            ende = time.monotonic() + self.max_warte
            while len(stapel) < self.max_stapel:
                try:
                    stapel.append(self.q.get_nowait())
                    continue
                except queue.Empty:
                    pass
                rest = ende - time.monotonic()
                if rest <= 0:
                    break
                try:
                    stapel.append(self.q.get(timeout=rest))
                except queue.Empty:
                    break
            t0 = time.monotonic()
            try:
                res = self.ent.entscheide_stapel([o for o, _ in stapel], self.schwelle)
            except Exception as e:
                res = [e] * len(stapel)
            self.sek_rechnen += time.monotonic() - t0
            self.n_anfragen += len(stapel)
            self.n_stapel += 1
            g = 1 << (len(stapel) - 1).bit_length()
            self.groessen[g] = self.groessen.get(g, 0) + 1
            for (_, f), r in zip(stapel, res):
                f.set_result(r)


A = None
_ST: Stapler | None = None
_ENT: StapelEntscheider | None = None
_zustand = {"netz": None, "step": 0, "mtime": 0.0}
_lade_lock = threading.Lock()


def neu_laden_falls_noetig() -> None:
    """Wie inf_d0: ändert sich die Checkpoint-Datei (mtime), Gewichte neu einlesen. Die RL-Schleife
    tauscht den Checkpoint je Iteration atomar aus und wartet, bis GET / den neuen Schritt meldet."""
    try:
        m = os.path.getmtime(A.ckpt)
    except OSError:
        return
    if m == _zustand["mtime"]:
        return
    with _lade_lock:
        if m == _zustand["mtime"]:
            return
        z = torch.load(A.ckpt, map_location="cpu", weights_only=True)
        with _ENT.lock:                                  # kein Stapel läuft während des Tauschs
            _ENT.ad.modul.load_state_dict(z["model"])
        _zustand.update(step=int(z.get("gstep", 0)), mtime=m)
        print(f"[inf_gpu] Checkpoint geladen, Schritt {_zustand['step']}", flush=True)


def main(argv=None):
    global A, _ST, _ENT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--port", type=int, default=8650)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--reputation", default=None)
    ap.add_argument("--kalibrierung", default=None)
    ap.add_argument("--schwelle", type=float, default=None)
    ap.add_argument("--decide-every", type=int, default=32)
    ap.add_argument("--max-stapel", type=int, default=64)
    ap.add_argument("--max-warte-ms", type=float, default=3.0)
    ap.add_argument("--ziehen", action="store_true", default=os.environ.get("D0_ZIEHEN", "0") == "1")
    ap.add_argument("--temp", type=float, default=float(os.environ.get("D0_TEMP", 1.0)))
    ap.add_argument("--top-k", type=int, default=int(os.environ.get("D0_TOP_K", 0)))
    ap.add_argument("--ziehen-koepfe", default=os.environ.get("D0_ZIEHEN_KOEPFE", "atype"))
    ap.add_argument("--wahl-saat", type=int,
                    default=int(os.environ["D0_WAHL_SAAT"]) if os.environ.get("D0_WAHL_SAAT") else None)
    A = ap.parse_args(argv)

    torch.backends.cudnn.allow_tf32 = False              # fp32 wie die Kalibrierung
    torch.backends.cuda.matmul.allow_tf32 = False
    A.schwelle_wert = A.schwelle
    if A.schwelle_wert is None and A.kalibrierung:
        with open(A.kalibrierung) as f:
            A.schwelle_wert = float(json.load(f)["schwelle"][str(A.decide_every)])
    if A.schwelle_wert is None:
        raise SystemExit("--schwelle oder --kalibrierung nötig")

    z = torch.load(A.ckpt, map_location="cpu", weights_only=True)
    netz = z.get("netz", "d1")
    if netz != "d1":
        raise SystemExit(f"inf_gpu.py kann nur D1, der Checkpoint ist {netz!r}")
    ad = N.baue_netz(netz)
    ad.modul.load_state_dict(z["model"])
    ad.modul.to(A.device)
    _zustand.update(netz=netz, step=int(z.get("gstep", 0)), mtime=os.path.getmtime(A.ckpt))
    wahl = SP.Wahl(ziehen=A.ziehen, temp=A.temp, top_k=A.top_k,
                   koepfe=tuple(x.strip() for x in A.ziehen_koepfe.split(",") if x.strip()), saat=A.wahl_saat)
    ent = StapelEntscheider(ad, A.device, D._rep_tabelle(A.reputation), wahl=wahl)
    ent.autocast_an = False
    _ENT = ent
    _ST = Stapler(ent, A.schwelle_wert, A.max_stapel, A.max_warte_ms / 1000.0)
    print(f"inf_gpu auf {A.host}:{A.port}, ckpt={A.ckpt}, Schritt {_zustand['step']}, device={A.device}, "
          f"Schwelle {A.schwelle_wert:.4f}, Stapel ≤ {A.max_stapel}, Warten ≤ {A.max_warte_ms} ms, {wahl}", flush=True)

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *x):
            pass

        def _senden(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_OPTIONS(self):
            self._senden(204, {})

        def do_GET(self):
            neu_laden_falls_noetig()
            if self.path.startswith("/stat"):
                s = _ST
                self._senden(200, {"anfragen": s.n_anfragen, "stapel": s.n_stapel,
                                   "sek_rechnen": round(s.sek_rechnen, 3), "groessen": s.groessen,
                                   "ms_je_anfrage": round(1000 * s.sek_rechnen / max(1, s.n_anfragen), 3)})
                return
            self._senden(200, {"ok": True, "netz": _zustand["netz"], "ckpt_step": _zustand["step"],
                               "zusatz": True, "zusatz_sig": SP.ZUSATZ_SIG, "decide_every": A.decide_every,
                               "schwelle": A.schwelle_wert, "ziehen": wahl.ziehen, "temp": wahl.temp,
                               "top_k": wahl.top_k, "ziehen_koepfe": ",".join(wahl.koepfe),
                               "wahl_saat": wahl.saat, "geraet": A.device, "stapel": True})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                o = json.loads(self.rfile.read(n))
                neu_laden_falls_noetig()
                res = _ST.anfrage(o)
                res["decide_every"] = A.decide_every
                res["netz"] = _zustand["netz"]
                self._senden(200, res)
            except Exception as e:
                self._senden(500, {"error": f"{type(e).__name__}: {e}"})

    http.server.ThreadingHTTPServer.request_queue_size = 512
    srv = http.server.ThreadingHTTPServer((A.host, A.port), H)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
