#!/usr/bin/env python3
"""RL-Schleife: spielen → abschliessen → AWR-Update mit Anker an bc3 → neuer Checkpoint → wiederholen;
am Ende die gepaarte Bewertung gegen bc3.

Je Iteration NN (Saat <name>iNN):
  1. spielen      K Inferenz-Server (inf_d0, eigene Ports) mit der aktuellen Politik, arena.ts mit
                  --spur: P Partien, je Partie --ki Kopien der Politik gegen Nations auf --stufe,
                  Karte, Bots. Sampling wie im Einsatz: Ziehen beim Aktionstyp, Top-k, Temperatur.
  2. abschliessen trajektorie.schliesse_partie je neue Partie (Belohnung, Vorteil A_t, .ok)
  3. trainieren   rl_train.py auf den letzten --puffer-iter Iterationen → <ckpt-dir>/<name>_iNN.pt
Danach bewerten: trainer/arena.py, letzte Politik gegen bc3 gepaart (gleiche Saat, --ki 1), Log nach
~/d1-lauf/logs/<name>_eval.log — so erscheint sie im LobsterBoard als Arena-Lauf.

Entscheide (erster Lauf rl1, 14.09.):
  - Anker: KL(bc3 ‖ π) auf den Zuständen der RL-Partien statt beigemischter Menschen-BC. Sie begrenzt
    die Abweichung genau dort, wo die Politik handelt, ist direkt messbar (KL je Iteration) und braucht
    keinen zweiten Lader über den Menschenpool. λ wird nachgeführt: KL am Ende der Runde über
    1,5 × --kl-ziel → λ × 1,5, unter --kl-ziel / 1,5 → λ / 1,5, in [--lam-min, --lam-max].
  - Wertkopf: Ziel R (Endplatz der Episode, die eigentliche Rückgabe ohne Formung), Eingang
    abgekoppelt. Er wird nicht benutzt (Φ ist die Baseline), lernt aber mit — Vorbereitung für
    einen Kritiker, gemessen als erklärte Varianz je Iteration.
  - Exploration nur beim Aktionstyp (Top-k-Ziehen); die übrigen Köpfe sind Argmax und bekommen
    keinen RL-Gradienten (Training auf eigenen Argmax-Labels wäre reines Schärfen).
  - P(handeln): feste Schwelle aus der bc3-Kalibrierung (k=32), kein RL-Gradient, auf der
    Log-Odds-Skala an bc3 gebunden. Beobachtet wird die Handlungsrate je Iteration.
  - Gegnerstufe ist genau ein Parameter: --stufe (ARENA_SCHWIERIGKEIT). Der User erhöht ihn selbst.

Start auf arch:
  cd ~/d1-lauf && setsid nohup ~/mat-dev/torchenv/bin/python trainer/rl_schleife.py --name rl1 \\
      --stufe Hard --bis 16:40 --ende 17:40 > logs/rl1.schleife.log 2>&1 < /dev/null &
Status: ~/d1-lauf/logs/<name>/status.json (liest lobster_push.py), Verlauf: …/verlauf.jsonl.
Anhalten: SIGTERM an die Schleife — sie beendet Kinder und Server, bewertet dann nicht mehr.
"""
from __future__ import annotations

import argparse
import datetime as dtm
import glob
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
import urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import arena as AR  # noqa: E402
import belohnung as B  # noqa: E402
import trajektorie as TJ  # noqa: E402

HOME = os.path.expanduser("~")
GESCHUETZT = {"bc2.pt", "bc3.pt"}


def argumente(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_argument_group("Lauf")
    g.add_argument("--name", default="rl1")
    g.add_argument("--start", default=f"{HOME}/mat-dev/netz-dev/checkpoints/bc3.pt")
    g.add_argument("--anker", default=f"{HOME}/mat-dev/netz-dev/checkpoints/bc3.pt")
    g.add_argument("--ckpt-dir", default=f"{HOME}/mat-dev/netz-dev/checkpoints")
    g.add_argument("--log-dir", default=f"{HOME}/d1-lauf/logs")
    g.add_argument("--spur-dir", default=f"{HOME}/d1-lauf/spuren")
    g.add_argument("--client", default=os.environ.get("ARENA_CLIENT", f"{HOME}/openfront-client-arena"))
    g.add_argument("--python", default=sys.executable)
    g.add_argument("--reputation", default=f"{HOME}/projects/openfront-ai/data/reputation.json")
    g.add_argument("--kalibrierung", default=f"{HOME}/d1-lauf/kal_bc3.json")
    g.add_argument("--iterationen", type=int, default=99, help="höchstens so viele")
    g.add_argument("--bis", default=None, help="HH:MM: keine neue Iteration, die danach enden würde")
    g.add_argument("--ende", default=None, help="HH:MM: harte Grenze, auch für die Bewertung")
    g = ap.add_argument_group("Partien")
    g.add_argument("--stufe", default="Hard", help="Nations-Schwierigkeit (ARENA_SCHWIERIGKEIT), fest")
    g.add_argument("--karte", default="World")
    g.add_argument("--bots", type=lambda x: x if x == "auto" else int(x), default=40,
                   help="Bots je Partie, oder auto: 100 auf kompakten, 400 auf normalen Karten (wie MapPlaylist)")
    g.add_argument("--groesse", default="Compact", choices=["Compact", "Normal"], help="Kartengrösse im Training")
    g.add_argument("--ticks", type=int, default=12000)
    g.add_argument("--ki", type=lambda x: x if x == "auto" else int(x), default=4,
                   help="Kopien der Politik je Partie, oder auto: Lobbygrösse je Karte wie im öffentlichen "
                        "Spiel (arena.ts --ki auto; Partien haben dann verschieden viele KIs)")
    g.add_argument("--partien", type=int, default=48, help="Partien je Iteration")
    g.add_argument("--jobs", type=int, default=12)
    g.add_argument("--server", type=int, default=3)
    g.add_argument("--gegner-pool", default=None,
                   help="Komma-Liste alter Checkpoints. Für jeden läuft ein eigener Inferenz-Server, "
                        "die Arena verteilt die KIs reihum über alle Server. So spielt die Politik gegen "
                        "frühere Versionen statt nur gegen sich selbst. Nur die KIs an den eigenen "
                        "--server Servern kommen in die Spur (arena.ts --spur-nur-srv); die Zeilen der "
                        "alten Netze stammen aus einer anderen Verhaltenspolitik und dürfen nicht ins Training")
    g.add_argument("--server-geraet", default="cpu")
    g.add_argument("--server-art", default="cpu", choices=["cpu", "gpu"],
                   help="cpu: inf_d0 (eine Anfrage nach der anderen); gpu: inf_gpu.py (Stapelbildung auf der "
                        "GPU, für Partien mit vielen KIs, --ki 125). Bei gpu sind 6–8 Server sinnvoll")
    g.add_argument("--server-threads", type=int, default=2)
    g.add_argument("--port-basis", type=int, default=8681, help="eigene Ports; 8650 bleibt unberührt")
    g.add_argument("--temp", type=float, default=1.0)
    g.add_argument("--top-k", type=int, default=4)
    g.add_argument("--ziehen-koepfe", default="atype",
                   help="Köpfe, die die Verhaltenspolitik zieht (inf_d0 D0_ZIEHEN_KOEPFE). Nur gezogene "
                        "Köpfe können aus dem AWR lernen")
    g = ap.add_argument_group("Training")
    g.add_argument("--puffer-iter", type=int, default=2, help="Trainingsdaten = die letzten N Iterationen")
    g.add_argument("--lam", type=float, default=1.0)
    g.add_argument("--lam-min", type=float, default=0.25)
    g.add_argument("--lam-max", type=float, default=8.0)
    g.add_argument("--kl-ziel", type=float, default=0.10, help="KL(bc3‖π) beim Aktionstyp, nats")
    g.add_argument("--ziel", default="platz", choices=B.ZIELE,
                   help="Endbelohnung R (belohnung.py): platz, sieg = 0,5·Platz + 0,5·Sieg, "
                        "gebiet = 0,5·Land/Land des Grössten + 0,5·Sieg")
    g.add_argument("--gae-lambda", type=float, default=None,
                   help="Vorteil nach GAE(λ) aus dem aufgezeichneten Wertkopf statt Monte Carlo "
                        "mit Φ als Basislinie; typisch 0.95. Ohne Angabe bleibt es wie bisher")
    g.add_argument("--awr-koepfe", default="atype",
                   help="Köpfe mit RL-Gradient (rl_train --awr-koepfe); sollte zu --ziehen-koepfe passen")
    g.add_argument("--phi", default=None,
                   help="Φ-Gewichte c,w_u,w_g statt belohnung.GEWICHTE_JE_ZIEL[--ziel], z. B. 0.75,0,0.25")
    g.add_argument("--train-arg", action="append", default=[], help="weitere Argumente für rl_train.py")
    g = ap.add_argument_group("Bewertung")
    g.add_argument("--eval-paare", type=int, default=200)
    g.add_argument("--eval-jobs", type=int, default=14)
    g.add_argument("--eval-server", type=int, default=3, help="inf_d0 je Seite (arena.py --server)")
    g.add_argument("--eval-threads", type=int, default=2)
    g.add_argument("--eval-port", type=int, default=8691, help="erster Port, dann +1 …")
    g.add_argument("--eval-saat", default=None, help="Standard <name>_eval, nie eine Trainingssaat")
    g.add_argument("--eval-gegner", default=None,
                   help="Checkpoint der Seite b in den Bewertungen (Standard --anker)")
    g.add_argument("--eval-spielweise", action="store_true",
                   help="Bewertungen zusätzlich mit Spielweise-Kennzahlen (arena.py --spielweise), "
                        "u. a. Truppen/Truppen-Max — Richtwert des Users sind 42 %")
    g.add_argument("--eval-wahl-saat", type=int, default=1,
                   help="Ziehen in Bewertungen an die Saat binden (inf_d0 --wahl-saat, beide Seiten dieselben "
                        "Zufallszahlen); -1 = ungekoppelt wie vor dem 14.09.")
    g.add_argument("--eval-dauer", type=float, default=900, help="Schätzung in s für die Planung")
    g.add_argument("--zwischen-alle", type=int, default=0, help="alle N Iterationen eine kleine Bewertung")
    g.add_argument("--zwischen-paare", type=int, default=60)
    return ap.parse_args(argv)


def uhrzeit(hhmm: str | None) -> float | None:
    if not hhmm:
        return None
    h, m = (int(x) for x in hhmm.split(":"))
    d = dtm.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    return d.timestamp()


def hm(t: float | None) -> str:
    return dtm.datetime.fromtimestamp(t).strftime("%H:%M") if t else "–"


def nachfahren(pid: int) -> list[int]:
    kinder: dict[int, list[int]] = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/stat") as f:
                s = f.read()
            kinder.setdefault(int(s[s.rindex(")") + 2:].split()[1]), []).append(int(p))
        except (OSError, ValueError, IndexError):
            continue
    out, stapel = [], [pid]
    while stapel:
        for c in kinder.get(stapel.pop(), []):
            out.append(c)
            stapel.append(c)
    return out


def baum_beenden(proc: subprocess.Popen | None, warte: float = 15.0) -> None:
    """Prozess samt allen Nachfahren (npx → node → Kinder) beenden."""
    if proc is None or proc.poll() is not None:
        return
    pids = [proc.pid] + nachfahren(proc.pid)
    for p in reversed(pids):
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
    t = time.time() + warte
    while time.time() < t and any(os.path.exists(f"/proc/{p}") for p in pids):
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
    for p in pids:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass


def get_json(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def zeilen_zahl(pfad: str) -> int:
    try:
        with open(pfad, "rb") as f:
            return sum(1 for z in f if z.strip())
    except OSError:
        return 0


def lies_jsonl(pfad: str) -> list[dict]:
    out = []
    try:
        with open(pfad) as f:
            for z in f:
                try:
                    out.append(json.loads(z))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


class Abbruch(Exception):
    pass


class Schleife:
    def __init__(self, a):
        self.a = a
        self.dir = os.path.join(a.log_dir, a.name)
        self.spur = os.path.join(a.spur_dir, a.name)
        os.makedirs(self.dir, exist_ok=True)
        os.makedirs(self.spur, exist_ok=True)
        self.status_pfad = os.path.join(self.dir, "status.json")
        self.verlauf_pfad = os.path.join(self.dir, "verlauf.jsonl")
        self.politik = os.path.join(self.dir, "politik.pt")
        self.pool = [os.path.expanduser(x.strip()) for x in (a.gegner_pool or "").split(",") if x.strip()]
        for c in self.pool:
            if not os.path.exists(c):
                raise SystemExit(f"--gegner-pool: {c} gibt es nicht")
        self.server: list[subprocess.Popen] = []
        self.kind: subprocess.Popen | None = None
        self.stopp: str | None = None
        self.bis, self.ende = uhrzeit(a.bis), uhrzeit(a.ende)
        with open(a.kalibrierung) as f:
            self.schwelle = float(json.load(f)["schwelle"]["32"])
        self.gew = B.gewichte_aus_text(a.phi) if a.phi else B.gewichte_fuer(a.ziel)
        self.t0 = time.time()
        self.st = {"name": a.name, "pid": os.getpid(), "seit": self.t0, "stufe": a.stufe, "karte": a.karte,
                   "bots": a.bots, "ki": a.ki, "partien_je_iteration": a.partien, "phase": "start",
                   "iteration": 0, "iterationen_fertig": 0, "bis": self.bis, "ende": self.ende,
                   "schwelle": self.schwelle, "lam": a.lam, "ziel": a.ziel,
                   "ziehen_koepfe": a.ziehen_koepfe, "awr_koepfe": a.awr_koepfe,
                   "gae_lambda": a.gae_lambda, "gegner_pool": [os.path.basename(c) for c in self.pool],
                   "phi": [self.gew.c, self.gew.w_u, self.gew.w_g], "letzte": None, "bewertung": None}
        self.dauern: list[float] = []
        with open(os.path.join(self.dir, "schleife.pid"), "w") as f:
            f.write(str(os.getpid()))

    # ------------------------------------------------------------ Status
    def melde(self, **k):
        self.st.update(k)
        self.st["t"] = time.time()
        self.st["eta_lauf"] = self.eta_lauf()
        tmp = self.status_pfad + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.st, f, ensure_ascii=False)
        os.replace(tmp, self.status_pfad)

    def eta_lauf(self) -> float | None:
        if self.st.get("phase") in ("fertig", "abgebrochen"):
            return None
        if self.st.get("phase") == "bewerten":
            return self.st.get("eta_phase")
        d_it = max(self.dauern[-2:]) if self.dauern else None
        if d_it is None or self.bis is None:
            return None
        # Iterationen, die noch in das Fenster bis --bis passen, dann die Bewertung
        t = time.time()
        if self.st.get("phase") in ("spielen", "abschliessen", "trainieren") and self.st.get("iteration_start"):
            t = max(t, self.st["iteration_start"] + d_it)
        while t + d_it <= self.bis and self.st.get("iteration", 0) + (t - time.time()) / d_it < self.a.iterationen:
            t += d_it
        return t + self.a.eval_dauer

    def pruefe_zeit(self, reserve: float = 0.0):
        if self.stopp:
            raise Abbruch(self.stopp)
        if self.ende and time.time() > self.ende - reserve:
            raise Abbruch("harte Zeitgrenze --ende")

    # ------------------------------------------------------------ Server
    def server_starten(self):
        env = dict(os.environ, D0_ZIEHEN="1", D0_TEMP=str(self.a.temp), D0_TOP_K=str(self.a.top_k),
                   D0_ZIEHEN_KOEPFE=self.a.ziehen_koepfe)
        env.pop("D0_WAHL_SAAT", None)
        for i in range(self.a.server):
            port = self.a.port_basis + i
            if port == 8650:
                raise SystemExit("Port 8650 ist tabu")
            if get_json(f"http://127.0.0.1:{port}/", 0.5) is not None:
                raise SystemExit(f"Port {port} ist schon belegt — nichts Fremdes anfassen")
            if self.a.server_art == "gpu":
                argv = [self.a.python, os.path.join(HIER, "inf_gpu.py"), "--ckpt", self.politik, "--port", str(port),
                        "--host", "127.0.0.1", "--device", "cuda",
                        "--decide-every", "32", "--schwelle", str(self.schwelle), "--reputation", self.a.reputation]
            else:
                argv = [self.a.python, os.path.join(HIER, "inf_d0.py"), "--ckpt", self.politik, "--port", str(port),
                        "--host", "127.0.0.1", "--device", self.a.server_geraet,
                        "--threads", str(self.a.server_threads),
                        "--decide-every", "32", "--schwelle", str(self.schwelle), "--reputation", self.a.reputation]
            log = open(os.path.join(self.dir, f"inf_{port}.log"), "a")
            self.server.append(subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env))
        self.urls = [f"http://127.0.0.1:{self.a.port_basis + i}/act" for i in range(self.a.server)]
        for j, ckpt in enumerate(self.pool):
            port = self.a.port_basis + self.a.server + j
            if port == 8650:
                raise SystemExit("Port 8650 ist tabu")
            if get_json(f"http://127.0.0.1:{port}/", 0.5) is not None:
                raise SystemExit(f"Port {port} ist schon belegt — nichts Fremdes anfassen")
            argv = [self.a.python, os.path.join(HIER, "inf_gpu.py" if self.a.server_art == "gpu" else "inf_d0.py"),
                    "--ckpt", ckpt, "--port", str(port), "--host", "127.0.0.1",
                    "--device", "cuda" if self.a.server_art == "gpu" else self.a.server_geraet,
                    "--decide-every", "32", "--schwelle", str(self.schwelle), "--reputation", self.a.reputation]
            if self.a.server_art == "cpu":
                argv += ["--threads", str(self.a.server_threads)]
            log = open(os.path.join(self.dir, f"inf_{port}.log"), "a")
            self.server.append(subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env))
            self.urls.append(f"http://127.0.0.1:{port}/act")

    def server_stoppen(self):
        for p in self.server:
            baum_beenden(p, 10)
        self.server = []

    def politik_setzen(self, ckpt: str, gstep: int):
        tmp = self.politik + ".tmp"
        shutil.copyfile(ckpt, tmp)
        os.replace(tmp, self.politik)
        if not self.server:
            self.server_starten()
        t = time.time() + 240
        offen = set(range(self.a.server))
        while offen:
            self.pruefe_zeit()
            for i in list(offen):
                if self.server[i].poll() is not None:
                    raise RuntimeError(f"inf_d0 auf Port {self.a.port_basis + i} ist gestorben")
                info = get_json(f"http://127.0.0.1:{self.a.port_basis + i}/", 30)
                if info and int(info.get("ckpt_step", -1)) == int(gstep):
                    offen.discard(i)
            if offen:
                if time.time() > t:
                    raise RuntimeError(f"Server laden den Schritt {gstep} nicht (offen {sorted(offen)})")
                time.sleep(1)

    # ------------------------------------------------------------ Phasen
    def warte_kind(self, fortschritt, alle: float = 5.0):
        while self.kind.poll() is None:
            try:
                self.pruefe_zeit()
            except Abbruch:
                baum_beenden(self.kind)
                raise
            fortschritt()
            time.sleep(alle)
        fortschritt()
        return self.kind.returncode

    def spielen(self, it: int, ckpt: str, gstep: int) -> list[dict]:
        a = self.a
        saat = f"{a.name}i{it:02d}"
        self.melde(phase="spielen", phase_fertig=0, phase_gesamt=a.partien, phase_einheit="Partien",
                   eta_phase=None, phase_start=time.time())
        self.politik_setzen(ckpt, gstep)
        aus = os.path.join(self.dir, f"i{it:02d}.jsonl")
        for p in [aus] + glob.glob(os.path.join(self.spur, f"{saat}-*")):
            if os.path.exists(p):
                os.unlink(p)                              # Wiederholung derselben Iteration: sauber neu
        AR.zusatz_modul_bereit(a.client)
        argv = ["npx", "tsx", "arena/arena.ts", "--inf", ",".join(self.urls), "--partien", str(a.partien),
                "--ticks", str(a.ticks), "--bots", str(a.bots), "--karte", a.karte, "--groesse", a.groesse,
                "--seiten", "netz", "--jobs", str(a.jobs), "--takt", "32", "--aus", aus, "--saat", saat,
                "--ki", str(a.ki), "--spur", os.path.abspath(self.spur)]
        if self.pool:
            argv += ["--spur-nur-srv", str(a.server)]
        env = dict(os.environ, ARENA_SCHWIERIGKEIT=a.stufe)
        out = open(os.path.join(self.dir, f"i{it:02d}.arena.log"), "w")
        t0 = time.time()
        self.kind = subprocess.Popen(argv, cwd=a.client, stdout=out, stderr=subprocess.STDOUT, env=env)

        def f():
            if a.ki == "auto":                # Zeilen je Partie verschieden: fertige Partien = Spurmarken
                n = len(glob.glob(os.path.join(self.spur, f"{saat}-*.spur.json")))
            else:
                n = zeilen_zahl(aus) // max(1, a.ki)
            eta = t0 + (time.time() - t0) * a.partien / n if n and time.time() - t0 > 20 else None
            self.melde(phase_fertig=n, eta_phase=eta)
        rc = self.warte_kind(f)
        zeilen = lies_jsonl(aus)
        if rc != 0 or not zeilen:
            raise RuntimeError(f"arena.ts Code {rc}, {len(zeilen)} Zeilen — siehe {out.name}")
        return zeilen

    def abschliessen(self, it: int) -> dict:
        saat = f"{self.a.name}i{it:02d}"
        self.melde(phase="abschliessen", phase_fertig=None, phase_gesamt=None, eta_phase=None)
        n, zeilen, fehler = 0, 0, []
        for m in sorted(glob.glob(os.path.join(self.spur, f"{saat}-*.spur.json"))):
            gid = os.path.basename(m)[: -len(".spur.json")]
            try:
                ok = TJ.schliesse_partie(self.spur, gid, self.gew, B.GAMMA, self.a.ziel, self.a.gae_lambda)
                n += 1
                zeilen += ok["samples"]
            except Exception as e:
                fehler.append(f"{gid}: {type(e).__name__}: {e}")
        if not n:
            raise RuntimeError(f"keine Partie abgeschlossen: {fehler[:3]}")
        return {"partien": n, "zeilen": zeilen, "fehler": fehler}

    def trainieren(self, it: int, init: str, neu: str, lam: float) -> dict:
        a = self.a
        if a.server_art == "gpu":
            # Die GPU-Server halten nach einer Spielphase mehrere GB VRAM (Allokator-Cache je Prozess);
            # 8 Server + Training ergaben am 20.09. CUDA out of memory im Zeigerkopf. Sie starten
            # beim nächsten Spielen von selbst neu (politik_setzen).
            self.server_stoppen()
        self.melde(phase="trainieren", phase_fertig=0, phase_gesamt=None, phase_einheit="Schritten",
                   eta_phase=None, phase_start=time.time())
        praef = [f"{a.name}i{j:02d}" for j in range(it, max(0, it - a.puffer_iter), -1)]
        fort = os.path.join(self.dir, f"i{it:02d}.fortschritt.json")
        bericht = os.path.join(self.dir, f"i{it:02d}.train.json")
        for p in (fort, bericht):
            if os.path.exists(p):
                os.unlink(p)
        argv = [a.python, os.path.join(HIER, "rl_train.py"), "--init", init, "--anker", a.anker,
                "--spur", self.spur, "--iterationen", ",".join(praef), "--aus", neu,
                "--reputation", a.reputation, "--name", f"{a.name}_i{it:02d}", "--iteration", str(it),
                "--lam", str(lam), "--awr-koepfe", a.awr_koepfe,
                "--fortschritt", fort, "--bericht", bericht] + a.train_arg
        out = open(os.path.join(self.dir, f"i{it:02d}.train.log"), "w")
        self.kind = subprocess.Popen(argv, cwd=os.path.dirname(HIER), stdout=out, stderr=subprocess.STDOUT)

        def f():
            try:
                with open(fort) as fh:
                    x = json.load(fh)
            except (OSError, ValueError):
                return
            s, g = x.get("schritt", 0), x.get("gesamt")
            eta = x["start"] + (x["t"] - x["start"]) * g / s if s and g else None
            self.melde(phase_fertig=s, phase_gesamt=g, eta_phase=eta)
        rc = self.warte_kind(f, 3.0)
        if rc != 0 or not os.path.exists(neu):
            raise RuntimeError(f"rl_train.py Code {rc} — siehe {out.name}")
        with open(bericht) as fh:
            return json.load(fh)

    def bewerten(self, ckpt: str, paare: int, name: str, saat: str) -> dict:
        a = self.a
        self.server_stoppen()                               # CPU für die Bewertung frei machen
        stamm = os.path.join(a.log_dir, name)
        for p in (stamm + ".a.jsonl", stamm + ".b.jsonl"):
            if os.path.exists(p):
                os.unlink(p)
        argv = [a.python, os.path.join(HIER, "arena.py"), "--ckpt", ckpt, "--b-ckpt", a.eval_gegner or a.anker,
                "--schwelle", str(self.schwelle), "--b-schwelle", str(self.schwelle),
                "--reputation", a.reputation, "--client", a.client, "--partien", str(paare),
                "--ticks", str(a.ticks), "--bots", str(eval_bots(a)), "--karte", a.karte, "--seiten", "netz",
                "--jobs", str(a.eval_jobs), "--takt", "32", "--saat", saat, "--ki", "1",
                "--port", str(a.eval_port), "--threads", str(a.eval_threads), "--server", str(a.eval_server),
                "--aus", stamm + ".jsonl"] + (["--spielweise"] if a.eval_spielweise else []) + [
                "--python", a.python, "--zeitlimit", "5400"]
        env = dict(os.environ, ARENA_SCHWIERIGKEIT=a.stufe, D0_ZIEHEN="1", D0_TEMP=str(a.temp),
                   D0_TOP_K=str(a.top_k), D0_ZIEHEN_KOEPFE=a.ziehen_koepfe)
        env.pop("D0_WAHL_SAAT", None)
        if a.eval_wahl_saat >= 0:
            # Gekoppelt: u hängt an Partie, Spieler, Tick und Kopf (arena.ts wahl_schluessel)
            env["D0_WAHL_SAAT"] = str(a.eval_wahl_saat)
        self.melde(phase="bewerten", phase_fertig=0, phase_gesamt=2 * paare, phase_einheit="Partien",
                   eta_phase=None, phase_start=time.time(), bewertung_name=name)
        out = open(stamm + ".log", "w")
        t0 = time.time()
        self.kind = subprocess.Popen(argv, cwd=os.path.dirname(HIER), stdout=out, stderr=subprocess.STDOUT, env=env)

        def f():
            n = zeilen_zahl(stamm + ".a.jsonl") + zeilen_zahl(stamm + ".b.jsonl")
            eta = t0 + (time.time() - t0) * 2 * paare / n if n and time.time() - t0 > 30 else None
            self.melde(phase_fertig=n, eta_phase=eta)
        rc = self.warte_kind(f)
        dauer = time.time() - t0
        out.close()
        with open(stamm + ".log") as fh:
            text = fh.read()
        erg = {"name": name, "paare": paare, "code": rc, "sekunden": round(dauer), "ckpt": ckpt,
               "gegner": a.eval_gegner or a.anker}
        m = re.search(r"^\s*platz\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)", text, re.M)
        if m:
            erg.update(platz_d_median=float(m.group(1)), rl_besser=int(m.group(2)), bc3_besser=int(m.group(3)),
                       p_vorzeichen=float(m.group(4)))
        a_ = lies_jsonl(stamm + ".a.jsonl")
        b_ = lies_jsonl(stamm + ".b.jsonl")
        if a_ and b_:
            erg["platz_median_rl"] = statistics.median(z["platz"] for z in a_)
            erg["platz_median_bc3"] = statistics.median(z["platz"] for z in b_)
            erg["gleich"] = paare - erg.get("rl_besser", 0) - erg.get("bc3_besser", 0)
            erg.update(sieg_vergleich(a_, b_))
        return erg

    # ------------------------------------------------------------ Zusammenfassung
    @staticmethod
    def partie_kennzahlen(zeilen: list[dict], ziel: str = "platz", nur_srv: int = 0) -> dict:
        # nur_srv > 0: nur die KIs an den ersten N Servern, also die eigene Politik (--gegner-pool)
        if nur_srv > 0:
            zeilen = [z for z in zeilen if z.get("srv", 0) < nur_srv] or zeilen
        pl = [z["platz"] for z in zeilen]
        # R wie im Training (belohnung.endwert); R_platz bleibt zwischen Läufen mit anderem Ziel vergleichbar
        R = [B.endwert(z, ziel) for z in zeilen]
        R_platz = [B.endwert(z, "platz") for z in zeilen]
        ak: dict[str, int] = {}
        for z in zeilen:
            for k, v in (z.get("aktionen") or {}).items():
                ak[k] = ak.get(k, 0) + v
        return {"episoden": len(zeilen), "platz_median": statistics.median(pl), "R_mittel": sum(R) / len(R),
                "R_platz_mittel": sum(R_platz) / len(R_platz),
                "ueberleben_median": statistics.median(z["ueberleben_ticks"] for z in zeilen),
                "tot": sum(1 for z in zeilen if z["abbruchgrund"] == "tot"),
                "siege": sum(1 for z in zeilen if z.get("sieg")),
                "handlungen_je_1000_median": statistics.median(z["handlungen_je_1000"] for z in zeilen),
                "aktionen": dict(sorted(ak.items(), key=lambda x: -x[1])[:12])}

    # ------------------------------------------------------------ Lauf
    def letzter_stand(self) -> tuple[int, str, float]:
        """Fortsetzen: höchste vorhandene <name>_iNN.pt."""
        best = (0, self.a.start, self.a.lam)
        for p in glob.glob(os.path.join(self.a.ckpt_dir, f"{self.a.name}_i*.pt")):
            m = re.search(r"_i(\d+)\.pt$", p)
            if m and int(m.group(1)) > best[0]:
                best = (int(m.group(1)), p, best[2])
        if best[0]:
            v = lies_jsonl(self.verlauf_pfad)
            if v and v[-1].get("lam_naechste"):
                best = (best[0], best[1], float(v[-1]["lam_naechste"]))
        return best

    def lauf(self):
        a = self.a
        import torch
        it0, ckpt, lam = self.letzter_stand()
        gstep = int(torch.load(ckpt, map_location="cpu", weights_only=True).get("gstep", 0))
        if it0:
            print(f"[fortsetzen] ab {ckpt} (Iteration {it0}), λ {lam}", flush=True)
        self.melde(iteration=it0, iterationen_fertig=it0, lam=lam)
        it = it0
        try:
            while it < a.iterationen:
                d_it = max(self.dauern[-2:]) if self.dauern else None
                if self.bis and d_it and time.time() + 1.1 * d_it > self.bis:
                    print(f"[plan] keine neue Iteration: {hm(time.time() + d_it)} läge nach --bis {hm(self.bis)}",
                          flush=True)
                    break
                if self.bis and time.time() > self.bis:
                    break
                it += 1
                t_it = time.time()
                self.melde(iteration=it, iteration_start=t_it)
                neu = os.path.join(a.ckpt_dir, f"{a.name}_i{it:02d}.pt")
                if os.path.basename(neu) in GESCHUETZT:
                    raise SystemExit("geschützter Name")
                zeilen = self.spielen(it, ckpt, gstep)
                t_sp = time.time()
                ab = self.abschliessen(it)
                self.pruefe_zeit()
                ber = self.trainieren(it, ckpt, neu, lam)
                kl = ber.get("kl_ende")
                lam_neu = lam
                if kl is not None:
                    if kl > 1.5 * a.kl_ziel:
                        lam_neu = min(a.lam_max, lam * 1.5)
                    elif kl < a.kl_ziel / 1.5:
                        lam_neu = max(a.lam_min, lam / 1.5)
                dauer = time.time() - t_it
                self.dauern.append(dauer)
                kz = self.partie_kennzahlen(zeilen, a.ziel, a.server if self.pool else 0)
                st = ber.get("stat", {})
                eintrag = {"iteration": it, "t": time.time(), "ckpt": neu, "politik": os.path.basename(ckpt),
                           "stufe": a.stufe, "ziel": a.ziel,
                           "phi": [self.gew.c, self.gew.w_u, self.gew.w_g],
                           "gae_lambda": a.gae_lambda, **kz, "entscheidungen": ab["zeilen"], "abschluss_fehler": len(ab["fehler"]),
                           "aktionszeilen_training": st.get("aktionszeilen"), "zeilen_training": st.get("zeilen"),
                           "schritte": ber.get("schritte"), "kl_ende": kl, "d_ob_ende": ber.get("d_ob_ende"),
                           "kl_k_ende": ber.get("kl_k_ende"), "kl_c_ende": ber.get("kl_c_ende"),
                           "H_ende": ber.get("H_ende"), "awr_ende": ber.get("awr_ende"),
                           "value_ende": ber.get("value_ende"), "erkl_var_wert": st.get("erkl_var_wert"),
                           "erkl_var_phi": st.get("erkl_var_phi"), "p_handeln_median": st.get("p_handeln_median"),
                           "w_gekappt": st.get("w_gekappt"), "lam": lam, "lam_naechste": lam_neu,
                           "sek_spielen": round(t_sp - t_it), "sek_training": ber.get("sekunden"),
                           "sek_gesamt": round(dauer)}
                with open(self.verlauf_pfad, "a") as fh:
                    fh.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
                print(f"[iteration {it}] Platz-Median {kz['platz_median']}, R {kz['R_mittel']:.3f}, "
                      f"Handlungen/1000 {kz['handlungen_je_1000_median']}, KL {kl}, λ {lam} → {lam_neu}, "
                      f"{dauer / 60:.1f} min", flush=True)
                ckpt, lam = neu, lam_neu
                gstep = int(torch.load(ckpt, map_location="cpu", weights_only=True).get("gstep", 0))
                self.melde(iterationen_fertig=it, lam=lam, letzte={k: eintrag[k] for k in (
                    "iteration", "platz_median", "R_mittel", "ueberleben_median", "handlungen_je_1000_median",
                    "kl_ende", "episoden", "entscheidungen")}, ckpt=os.path.basename(ckpt))
                if a.zwischen_alle and it % a.zwischen_alle == 0 and it < a.iterationen:
                    z = self.bewerten(ckpt, a.zwischen_paare, f"{a.name}_z{it:02d}", f"{a.name}_zwischen")
                    with open(self.verlauf_pfad, "a") as fh:
                        fh.write(json.dumps({"zwischenbewertung": z, "iteration": it, "t": time.time()}) + "\n")
                    self.melde(zwischen=z)
        except Abbruch as e:
            print(f"[abbruch] {e} — letzte fertige Politik {ckpt}", flush=True)
            if self.stopp or (self.ende and time.time() > self.ende - 60):
                self.aufraeumen("abgebrochen", str(e))
                return
        finally:
            self.server_stoppen()
        if ckpt == a.start:
            self.aufraeumen("abgebrochen", "keine Iteration fertig")
            return
        # ---- Bewertung der letzten Politik, vorher festgelegt: 200 Paare, nur Platz als Hauptkennzahl
        if a.eval_paare <= 0:
            self.aufraeumen("fertig", "ohne Bewertung (--eval-paare 0)")
            return
        try:
            self.pruefe_zeit(reserve=0)
            erg = self.bewerten(ckpt, a.eval_paare, f"{a.name}_eval", a.eval_saat or f"{a.name}_eval")
            with open(self.verlauf_pfad, "a") as fh:
                fh.write(json.dumps({"bewertung": erg, "t": time.time()}, ensure_ascii=False) + "\n")
            print(f"[bewertung] {json.dumps(erg, ensure_ascii=False)}", flush=True)
            self.aufraeumen("fertig", None, bewertung=erg)
        except Abbruch as e:
            print(f"[abbruch] Bewertung: {e}", flush=True)
            self.aufraeumen("abgebrochen", f"Bewertung: {e}")

    def aufraeumen(self, phase: str, grund: str | None, **k):
        baum_beenden(self.kind)
        self.server_stoppen()
        for p in glob.glob(self.politik + "*"):
            os.unlink(p)
        self.melde(phase=phase, grund=grund, eta_phase=None, phase_fertig=None, phase_gesamt=None, **k)


def eval_bots(a) -> int:
    """Bots in den Bewertungen. Training mit --bots auto (Lobby wie im Spielplan) bewertet trotzdem
    fest mit 400 Bots und einer KI, so wie alle Messpunkte gegen ur1 seit rl8 -- sonst wäre die
    Leiter nicht vergleichbar. trainer/arena.py kennt ohnehin nur Zahlen (am 21.09. brachen deshalb
    die Messpunkte z10-z30 von rl10 nach 5 s ab)."""
    return 400 if a.bots == "auto" else int(a.bots)


def sieg_vergleich(a_: list[dict], b_: list[dict]) -> dict:
    """Siege gepaart über spiel_id (gleiche Saat auf beiden Seiten): McNemar exakt, zweiseitig."""
    sa = {z["spiel_id"]: bool(z.get("sieg")) for z in a_}
    sb = {z["spiel_id"]: bool(z.get("sieg")) for z in b_}
    ids = sorted(set(sa) & set(sb))
    nur_a = sum(1 for i in ids if sa[i] and not sb[i])
    nur_b = sum(1 for i in ids if sb[i] and not sa[i])
    return {"sieg_paare": len(ids), "siege_a": sum(sa[i] for i in ids), "siege_b": sum(sb[i] for i in ids),
            "sieg_nur_a": nur_a, "sieg_nur_b": nur_b, "p_sieg_mcnemar": mcnemar_p(nur_a, nur_b)}


def mcnemar_p(n1: int, n2: int) -> float:
    """Exakter McNemar-Test (Binomial mit p = 0,5 auf den ungleichen Paaren), zweiseitig."""
    n, k = n1 + n2, min(n1, n2)
    if n == 0:
        return 1.0
    from math import comb
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def main(argv=None):
    a = argumente(argv)
    s = Schleife(a)

    def halt(sig, _f):
        s.stopp = signal.Signals(sig).name
        print(f"[signal] {s.stopp}: breche ab", flush=True)
    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    try:
        s.lauf()
    except BaseException as e:
        import traceback
        traceback.print_exc()
        s.aufraeumen("abgebrochen", f"{type(e).__name__}: {e}")
        raise
    print("fertig.", flush=True)


if __name__ == "__main__":
    main()
