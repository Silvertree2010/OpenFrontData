#!/usr/bin/env python3
"""Inferenz-Server für D0 und D1, gleiches Anfrageformat wie env/inf_server.py (POST /act, GET /).

Welches Netz, steht im Checkpoint ("netz": "d0" | "d1"); --netz legt es ausdrücklich fest
und bricht ab, wenn der Checkpoint etwas anderes sagt. D1 verlangt in jeder Anfrage die
Zusatzfelder (zusatz_b64, zusatz_sig; spielen.zusatz_aus_anfrage). GET / meldet "netz" und
"zusatz", damit Arena und Erweiterung wissen, was der Server braucht.

Lädt den Checkpoint neu, sobald er sich ändert (folgt einem laufenden Training). Die
Entscheidung rechnet spielen.Entscheider (Schwelle für P(handeln), argmax, Top-5-Zellen).
Die Schwelle hängt davon ab, wie oft der Viewer fragt (DECIDE_EVERY = k): sie kommt aus
kalibriere_schwelle.py (--kalibrierung, Eintrag für --decide-every) oder direkt aus --schwelle.
Jede Antwort nennt decide_every; der Viewer übernimmt den Wert.

  python trainer/inf_d0.py --ckpt checkpoints/bc2.pt --kalibrierung kalibrierung.json \\
      --port 8650 --host 0.0.0.0 --device cpu --reputation …/reputation.json
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import threading
import time

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import torch  # noqa: E402

import daten as D  # noqa: E402
import netze as N  # noqa: E402
import spielen as SP  # noqa: E402

A = None
_lock = threading.Lock()
_zustand = {"ent": None, "mtime": -1.0, "step": 0, "netz": "d0"}


def entscheider():
    with _lock:
        m = os.path.getmtime(A.ckpt) if os.path.exists(A.ckpt) else 0.0
        if _zustand["ent"] is None or m > _zustand["mtime"]:
            z = torch.load(A.ckpt, map_location="cpu", weights_only=True) if os.path.exists(A.ckpt) else None
            im_ckpt = z.get("netz", "d0") if z is not None else None
            netz = A.netz if A.netz != "auto" else (im_ckpt or "d0")
            if im_ckpt is not None and im_ckpt != netz:
                raise ValueError(f"{A.ckpt} gehört zu Netz {im_ckpt!r}, gewählt ist {netz!r}")
            if netz not in ("d0", "d1"):
                raise ValueError(f"Netz {netz!r}: der Server kann d0 und d1")
            ad = N.baue_netz(netz)                      # d1: Breiten der Zusatzfelder wie train.py
            if z is not None:
                ad.modul.load_state_dict(z["model"])
                _zustand["step"] = int(z.get("gstep", 0))
            ad.modul.to(A.device)
            _zustand["ent"] = SP.Entscheider(ad, A.device, D._rep_tabelle(A.reputation), wahl=A.wahl)
            _zustand["mtime"] = m
            _zustand["netz"] = netz
            print(f"[inf_d0] Checkpoint geladen, Netz {netz}, Schritt {_zustand['step']}", flush=True)
    return _zustand["ent"]


def schwelle_aus(a) -> float:
    if a.schwelle is not None:
        return a.schwelle
    if a.kalibrierung:
        with open(a.kalibrierung) as f:
            kal = json.load(f)
        tab = {int(k): float(v) for k, v in kal["schwelle"].items()}
        if a.decide_every not in tab:
            raise SystemExit(f"keine Schwelle für decide_every={a.decide_every} in {a.kalibrierung}: {sorted(tab)}")
        return tab[a.decide_every]
    raise SystemExit("--schwelle oder --kalibrierung angeben")


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *x):
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
        entscheider()
        zus = _zustand["netz"] == "d1"
        self._send(200, {"ok": True, "netz": _zustand["netz"], "ckpt_step": _zustand["step"],
                         "zusatz": zus, **({"zusatz_sig": SP.ZUSATZ_SIG} if zus else {}),
                         "decide_every": A.decide_every, "schwelle": A.schwelle_wert,
                         "ziehen": A.ziehen, "temp": A.temp, "top_k": A.top_k,
                         "ziehen_koepfe": A.ziehen_koepfe, "wahl_saat": A.wahl_saat})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            o = json.loads(self.rfile.read(n))
            res = entscheider().entscheide(o, A.schwelle_wert)
            if A.mitschrift:
                mitschreiben(A.mitschrift, o, res, self.client_address[0])
            res["decide_every"] = A.decide_every
            res["netz"] = _zustand["netz"]
            if res["atype"] != "NO_OP" and not self.client_address[0].startswith("127."):
                print(f"[act] {self.client_address[0]} -> {res['atype']} {res.get('einheit') or ''} "
                      f"p={res['p_handeln']:.3f} {res.get('grund', '')}", flush=True)
            self._send(200, res)
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def mitschreiben(pfad: str, anfrage: dict, antwort: dict, von: str) -> None:
    """Eine Zeile JSON je Anfrage: alles ausser den grossen Feldern (Karte, Zellfakten, Vektoren).
    Für den Vergleich Live gegen Arena — dort kommt dieselbe Anfrage aus arena.ts."""
    GROSS = ("map", "cells", "cellFacts", "zellen", "vec", "vektor", "obs", "zusatz_vec")
    klein = {}
    for k, v in anfrage.items():
        if k in GROSS:
            klein[k] = f"<{type(v).__name__}, {len(v) if hasattr(v, '__len__') else '?'}>"
        elif isinstance(v, (list, str)) and len(v) > 64:
            klein[k] = f"<{type(v).__name__}, {len(v)}>"
        else:
            klein[k] = v
    zeile = {"t": round(time.time(), 3), "von": von, "anfrage": klein, "antwort": antwort}
    try:
        with open(pfad, "a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False, default=str) + "\n")
    except OSError as e:      # eine kaputte Mitschrift darf keine Partie stoppen
        print(f"[mitschrift] {type(e).__name__}: {e}", flush=True)


def main(argv=None):
    global A
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", default="checkpoints/bc2.pt")
    ap.add_argument("--netz", default="auto", choices=["auto", "d0", "d1"],
                    help="auto: wie im Checkpoint vermerkt (fehlt der Checkpoint: d0)")
    ap.add_argument("--port", type=int, default=8650)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--reputation", default=None)
    ap.add_argument("--kalibrierung", default=None, help="JSON aus kalibriere_schwelle.py")
    ap.add_argument("--schwelle", type=float, default=None, help="feste Schwelle für P(handeln)")
    ap.add_argument("--mitschrift", default=os.environ.get("D0_MITSCHRIFT"),
                    help="JSONL: je Anfrage eine Zeile mit Anfrage (ohne Karte/Zellen) und Antwort. "
                         "Für die Fehlersuche am Live-Weg (Erweiterung) gegen die Arena. "
                         "Geht auch über die Umgebung (D0_MITSCHRIFT), dann schreibt auch die Arena mit")
    ap.add_argument("--decide-every", type=int, default=32,
                    help="so oft fragt der Viewer (Ticks); 32 gewählt nach der Kalibrierung vom 11.09.")
    # Diese vier gehen auch über die Umgebung (D0_ZIEHEN, D0_TEMP, D0_TOP_K, D0_ZIEHEN_KOEPFE,
    # D0_WAHL_SAAT). trainer/arena.py startet inf_d0 selbst und reicht nur --schwelle durch;
    # über die Umgebung lässt sich Ziehen gegen Argmax vergleichen, ohne die Arena zu ändern.
    ap.add_argument("--ziehen", action="store_true", default=os.environ.get("D0_ZIEHEN", "0") == "1",
                    help="Aktionstyp (und weitere Köpfe aus --ziehen-koepfe) aus der Verteilung ziehen "
                         "statt argmax. Die Schwelle für Nichtstun bleibt davor unberührt")
    ap.add_argument("--temp", type=float, default=float(os.environ.get("D0_TEMP", 1.0)),
                    help="Temperatur beim Ziehen")
    ap.add_argument("--top-k", type=int, default=int(os.environ.get("D0_TOP_K", 0)),
                    help="nur unter den k wahrscheinlichsten ziehen (0 = alle)")
    ap.add_argument("--ziehen-koepfe", default=os.environ.get("D0_ZIEHEN_KOEPFE", "atype"),
                    help="Komma-Liste: atype, unit_type, target")
    ap.add_argument("--wahl-saat", type=int, default=(int(os.environ["D0_WAHL_SAAT"])
                                                      if os.environ.get("D0_WAHL_SAAT") else None),
                    help="Saat fürs Ziehen (reproduzierbar)")
    A = ap.parse_args(argv)
    torch.set_num_threads(A.threads)
    A.schwelle_wert = schwelle_aus(A)
    A.wahl = SP.Wahl(ziehen=A.ziehen, temp=A.temp, top_k=A.top_k,
                     koepfe=tuple(x.strip() for x in A.ziehen_koepfe.split(",") if x.strip()),
                     saat=A.wahl_saat)
    print(f"inf_d0 auf {A.host}:{A.port}, ckpt={A.ckpt}, device={A.device}, "
          f"decide_every={A.decide_every}, Schwelle P(handeln)={A.schwelle_wert:.4f}, {A.wahl}", flush=True)
    entscheider()
    http.server.ThreadingHTTPServer((A.host, A.port), H).serve_forever()


if __name__ == "__main__":
    main()
