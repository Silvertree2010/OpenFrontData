#!/usr/bin/env python3
"""Arena aus dem Trainer heraus: ein paar Partien mit einem Zwischenstand, Kennzahlen zurück.

Der Trainer ruft alle paar tausend Schritte

    from arena import arena_lauf
    erg = arena_lauf("checkpoints/d0.pt", partien=8, ticks=3000)
    board.melde(erg["seiten"]["netz"]["gebiet_1000_median"], ...)

und bekommt das JSON, das `arena/arena.ts` auf stdout schreibt.

Was hier passiert:
 1. Der Checkpoint wird kopiert, bevor der Server ihn liest. Der Trainer schreibt
    währenddessen weiter; eine halb geschriebene Datei würde den Server umbringen oder,
    schlimmer, still ein anderes Netz messen lassen.
 2. `trainer/inf_d0.py` läuft als eigener Prozess auf CPU und bleibt zwischen den Aufrufen
    am Leben — er lädt die Kopie neu, sobald sie sich ändert (`os.path.getmtime`). Neu
    starten müsste er nur bei anderer Kalibrierung; das kostet sonst jedes Mal das
    Hochfahren von torch.
 3. `npx tsx arena/arena.ts` läuft im gepatchten Client (`--client`, sonst `$ARENA_CLIENT`).

Aufruf von Hand:
    python3 trainer/arena.py --ckpt checkpoints/d0.pt --kalibrierung kalibrierung.json \\
        --client ~/openfront-client --partien 8 --ticks 3000 --jobs 4 \\
        --aus /tmp/arena.jsonl --reputation data/reputation.json

Netz D1: inf_d0 erkennt das Netz am Checkpoint. Die Zusatzfelder rechnet arena.ts mit
zusatz/src/felder.ts aus DIESEM Repo; die Datei wird vor jedem Lauf byte-gleich als
<client>/arena/zusatzFelder.ts hingelegt (keine zweite Fassung im Client).

Zwei Netze gegeneinander (gepaart: dieselbe --saat, also dieselben Partien):
    python3 trainer/arena.py --ckpt checkpoints/bc3.pt --schwelle S3 \\
        --b-ckpt checkpoints/bc2.pt --b-schwelle 0.016 --seiten netz --aus /tmp/v.jsonl …
schreibt /tmp/v.a.jsonl und /tmp/v.b.jsonl und ruft arena/auswertung.py A gegen B.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
ZUSATZ_FELDER = os.path.join(os.path.dirname(HIER), "zusatz", "src", "felder.ts")

_server: dict = {"procs": [], "ports": [], "ckpt_kopie": None, "schluessel": None}


def zusatz_modul_bereit(client: str) -> str:
    """zusatz/src/felder.ts byte-gleich nach <client>/arena/zusatzFelder.ts (nur wenn anders)."""
    if not os.path.isfile(ZUSATZ_FELDER):
        raise FileNotFoundError(f"{ZUSATZ_FELDER} fehlt — ohne das Modul kann arena.ts keine Zusatzfelder rechnen")
    ziel = os.path.join(client, "arena", "zusatzFelder.ts")
    with open(ZUSATZ_FELDER, "rb") as f:
        neu = f.read()
    alt = None
    if os.path.exists(ziel):
        with open(ziel, "rb") as f:
            alt = f.read()
    if alt != neu:
        tmp = ziel + ".tmp"
        with open(tmp, "wb") as f:
            f.write(neu)
        os.replace(tmp, ziel)
    return ziel


def _antwortet(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _server_stoppen() -> None:
    procs = _server.get("procs") or []
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    _server["procs"], _server["ports"] = [], []
    k = _server.get("ckpt_kopie")
    if k and os.path.exists(k):
        try:
            os.unlink(k)
        except OSError:
            pass


atexit.register(_server_stoppen)


def server_bereit(ckpt: str, kalibrierung: str | None, schwelle: float | None,
                  reputation: str | None, port: int, takt: int, device: str,
                  threads: int, python: str | None = None, anzahl: int = 1,
                  wahl_saat: int | None = None) -> tuple[str, dict]:
    """Startet inf_d0 mit einer Kopie des Checkpoints oder frischt die Kopie auf.

    anzahl > 1: so viele Server auf port, port+1, … mit derselben Kopie. Ein inf_d0 rechnet
    eine Anfrage nach der anderen; arena.ts verteilt die Partien reihum auf die Liste (--inf
    mit Kommas). Am Ergebnis ändert das nichts, nur am Tempo.

    Rückgabe: (URL(s) für --inf, durch Kommas getrennt; GET-Antwort des ersten Servers).
    """
    schluessel = (os.path.abspath(ckpt), kalibrierung, schwelle, reputation, takt, device, port, anzahl,
                  wahl_saat)
    procs = _server["procs"]
    lebt = bool(procs) and all(p.poll() is None for p in procs)
    if procs and (not lebt or _server["schluessel"] != schluessel):
        _server_stoppen()
        lebt = False

    if not lebt:
        fd, kopie = tempfile.mkstemp(prefix="arena_ckpt_", suffix=".pt")
        os.close(fd)
        _server["ckpt_kopie"] = kopie
    kopie = _server["ckpt_kopie"]
    shutil.copyfile(ckpt, kopie)          # immer frisch: inf_d0 lädt bei neuer mtime nach

    if not lebt:
        for i in range(max(1, anzahl)):
            argv = [python or sys.executable, os.path.join(HIER, "inf_d0.py"),
                    "--ckpt", kopie, "--port", str(port + i), "--host", "127.0.0.1",
                    "--device", device, "--threads", str(threads), "--decide-every", str(takt)]
            if kalibrierung:
                argv += ["--kalibrierung", kalibrierung]
            if schwelle is not None:
                argv += ["--schwelle", str(schwelle)]
            if reputation:
                argv += ["--reputation", reputation]
            if wahl_saat is not None:
                argv += ["--wahl-saat", str(wahl_saat)]
            _server["procs"].append(subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                                     stderr=subprocess.STDOUT))
            _server["ports"].append(port + i)
        _server["schluessel"] = schluessel

    erste = None
    for pt, p in zip(_server["ports"], _server["procs"]):
        for _ in range(600):              # torch braucht beim ersten Start seine Zeit
            info = _antwortet(pt)
            if info is not None:
                erste = erste or info
                break
            if p.poll() is not None:
                raise RuntimeError(f"inf_d0 auf Port {pt} ist beim Start gestorben (Code {p.returncode})")
            time.sleep(0.25)
        else:
            raise RuntimeError(f"inf_d0 auf Port {pt} antwortet nach 150 s nicht")
    return ",".join(f"http://127.0.0.1:{pt}/act" for pt in _server["ports"]), erste


def arena_lauf(ckpt: str, *, client: str | None = None, partien: int = 8, ticks: int = 3000,
               bots: int = 40, karte: str = "World", groesse: str = "Compact",
               saat: str | None = None, seiten: str = "netz,nichtstun", jobs: int = 4,
               takt: int = 32, aus: str | None = None, kalibrierung: str | None = None,
               schwelle: float | None = None, reputation: str | None = None,
               port: int = 8659, device: str = "cpu", threads: int = 2,
               python: str | None = None, zeitlimit: float = 3600.0, ki: int = 1,
               aufnahme: str | None = None, spur: str | None = None,
               spur_kennung: str | None = None, server: int = 1,
               wahl_saat: int | None = None) -> dict:
    """Ein paar Partien mit `ckpt` spielen und die Zusammenfassung zurückgeben.

    `saat` bestimmt die Spiel-IDs. Für einen Vergleich über die Trainingszeit muss sie
    konstant bleiben — dann spielt jeder Zwischenstand dieselben Partien, und der
    Unterschied liegt am Netz und nicht an der Auslosung.

    `ki`: KI-Spieler je Partie (alle mit diesem Netz, ein Server). `aufnahme`: Ordner
    (absolut), in den arena.ts je Partie einen GameRecord schreibt — als Replay abspielbar,
    siehe viewer/arena/aufnahme_server.mjs im Arena-Repo.

    `spur`: Ordner für Trajektorien (arena/spur.ts); danach schliesst trajektorie.py sie ab
    (Belohnung, Vorteile, .ok) — Ergebnis unter erg["spur"]. `spur_kennung` hängt "_K" an die gid.
    """
    client = client or os.environ.get("ARENA_CLIENT")
    if not client:
        raise ValueError("--client (oder $ARENA_CLIENT) muss auf den gepatchten Client zeigen")
    if not os.path.isfile(os.path.join(client, "arena", "arena.ts")):
        raise FileNotFoundError(f"{client}/arena/arena.ts fehlt — viewer/arena/ dorthin kopieren")
    zusatz_modul_bereit(client)

    inf, info = server_bereit(ckpt, kalibrierung, schwelle, reputation, port, takt,
                              device, threads, python, anzahl=server, wahl_saat=wahl_saat)
    aus = aus or os.path.join(tempfile.gettempdir(), f"arena_{os.getpid()}.jsonl")
    argv = ["npx", "tsx", "arena/arena.ts", "--inf", inf, "--partien", str(partien),
            "--ticks", str(ticks), "--bots", str(bots), "--karte", karte,
            "--groesse", groesse, "--seiten", seiten, "--jobs", str(jobs),
            "--takt", str(takt), "--aus", aus, "--saat", saat or "arena", "--ki", str(ki)]
    if aufnahme:
        argv += ["--aufnahme", os.path.abspath(aufnahme)]
    if spur:
        argv += ["--spur", os.path.abspath(spur)]
        if spur_kennung:
            argv += ["--spur-kennung", spur_kennung]
    t0 = time.time()
    p = subprocess.run(argv, cwd=client, capture_output=True, text=True, timeout=zeitlimit)
    letzte = [l for l in p.stdout.splitlines() if l.strip()]
    if not letzte:
        return {"ok": False, "fehler": "arena.ts hat nichts ausgegeben",
                "stderr": p.stderr[-2000:]}
    try:
        erg = json.loads(letzte[-1])
    except json.JSONDecodeError:
        return {"ok": False, "fehler": "letzte Zeile ist kein JSON: " + letzte[-1][:300],
                "stderr": p.stderr[-2000:]}
    erg["jsonl"] = aus
    erg["sekunden"] = round(time.time() - t0, 1)
    erg["server"] = info
    if spur:
        import trajektorie
        erg["spur"] = trajektorie.abschliessen(os.path.abspath(spur))
    return erg


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--client", default=None)
    ap.add_argument("--partien", type=int, default=8)
    ap.add_argument("--ticks", type=int, default=3000)
    ap.add_argument("--bots", type=int, default=40)
    ap.add_argument("--karte", default="World")
    ap.add_argument("--groesse", default="Compact")
    ap.add_argument("--saat", default="arena")
    ap.add_argument("--seiten", default="netz,nichtstun")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--takt", type=int, default=32)
    ap.add_argument("--aus", default=None)
    ap.add_argument("--kalibrierung", default=None)
    ap.add_argument("--schwelle", type=float, default=None)
    ap.add_argument("--reputation", default=None)
    ap.add_argument("--port", type=int, default=8659)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--server", type=int, default=1,
                    help="so viele inf_d0 (Ports --port, --port+1, …); arena.ts verteilt die Partien reihum")
    ap.add_argument("--python", default=None, help="Python mit torch, falls nicht dieses")
    ap.add_argument("--ki", type=int, default=1, help="KI-Spieler je Partie (alle mit --ckpt)")
    ap.add_argument("--aufnahme", default=None,
                    help="Ordner für GameRecords (Replay im Client), je Partie <spielId>.json")
    ap.add_argument("--zeitlimit", type=float, default=3600.0, help="Sekunden für den ganzen arena.ts-Lauf")
    ap.add_argument("--spur", default=None,
                    help="Ordner für Trajektorien (gepaart: SPUR/a und SPUR/b, gid mit _a/_b)")
    ap.add_argument("--spur-kennung", default=None, help="an die gid gehängt (_K)")
    ap.add_argument("--b-ckpt", default=None,
                    help="zweites Netz, gepaart gegen --ckpt (gleiche Saat); dann läuft auswertung.py")
    ap.add_argument("--b-kalibrierung", default=None)
    ap.add_argument("--b-schwelle", type=float, default=None)
    ap.add_argument("--wahl-saat", type=int,
                    default=int(os.environ["D0_WAHL_SAAT"]) if os.environ.get("D0_WAHL_SAAT") else None,
                    help="Ziehen an die Saat binden (inf_d0 --wahl-saat): u je Partie, Spieler, Tick und Kopf, "
                         "auf beiden Seiten eines Paars gleich. Ohne: wie bisher ungekoppelt")
    ap.add_argument("--spielweise", action="store_true",
                    help="nach einem Paar zusätzlich <aus>.spielweise.json (auswertung.py --json-aus) schreiben")
    A = ap.parse_args(argv)
    gemein = dict(client=A.client, partien=A.partien, ticks=A.ticks, bots=A.bots,
                  karte=A.karte, groesse=A.groesse, saat=A.saat, seiten=A.seiten,
                  jobs=A.jobs, takt=A.takt, reputation=A.reputation, port=A.port,
                  device=A.device, threads=A.threads, python=A.python, ki=A.ki,
                  aufnahme=A.aufnahme, zeitlimit=A.zeitlimit, server=A.server, wahl_saat=A.wahl_saat)
    if not A.b_ckpt:
        erg = arena_lauf(A.ckpt, aus=A.aus, kalibrierung=A.kalibrierung, schwelle=A.schwelle,
                         spur=A.spur, spur_kennung=A.spur_kennung, **gemein)
        print(json.dumps(erg, ensure_ascii=False, indent=1))
        return 0 if erg.get("ok") else 1

    # Zwei Netze nacheinander auf denselben Partien; der Server wechselt dazwischen den Checkpoint.
    stamm = (A.aus or os.path.join(tempfile.gettempdir(), f"arena_{os.getpid()}.jsonl"))
    stamm = stamm[:-6] if stamm.endswith(".jsonl") else stamm
    erg_a = arena_lauf(A.ckpt, aus=stamm + ".a.jsonl", kalibrierung=A.kalibrierung,
                       schwelle=A.schwelle, spur=A.spur and os.path.join(A.spur, "a"),
                       spur_kennung=A.spur and "a", **gemein)
    erg_b = arena_lauf(A.b_ckpt, aus=stamm + ".b.jsonl", kalibrierung=A.b_kalibrierung,
                       schwelle=A.b_schwelle, spur=A.spur and os.path.join(A.spur, "b"),
                       spur_kennung=A.spur and "b", **gemein)
    print(json.dumps({"a": erg_a, "b": erg_b}, ensure_ascii=False, indent=1))
    if not (erg_a.get("ok") and erg_b.get("ok")):
        return 1
    client = A.client or os.environ.get("ARENA_CLIENT")
    zusatz = ["--json-aus", stamm + ".spielweise.json"] if A.spielweise else []
    p = subprocess.run([sys.executable, os.path.join(client, "arena", "auswertung.py"),
                        stamm + ".a.jsonl", stamm + ".b.jsonl", "--a-seite", "netz", "--b-seite", "netz",
                        *zusatz], capture_output=True, text=True)
    print(p.stdout + p.stderr)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
