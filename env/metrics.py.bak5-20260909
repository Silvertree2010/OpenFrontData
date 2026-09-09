"""Trainingsmetriken als JSONL schreiben und gedrosselt nach apollo schieben.

Der Trainer laeuft auf dem Arch (RTX 5080), die oeffentliche Live-Seite auf apollo.
Darum: lokal anhaengen (billig, ueberlebt Abstuerze) und hoechstens alle paar
Sekunden per rsync hochschieben. Netzfehler sind egal — das Training darf daran
NIE haengen bleiben, deshalb Hintergrund-Thread mit Timeout und geschlucktem Fehler.

    from metrics import MetricsLog
    ml = MetricsLog(label="echte Shards", device=dev, steps_total=a.steps)
    ml.log(step, loss.item(), acc=key)      # acc optional, "step"-Zeile
    ml.event("start", label=..., epochs=..., ...)   # weitere Ereignisarten
    ml.push_now()
    ml.close()

Abschalten: OF_METRICS_PUSH=0 (schreibt dann nur lokal).
"""
import json, os, subprocess, threading, time

REMOTE      = os.environ.get("OF_METRICS_REMOTE",
                             "apollo:/home/netter/of-work/train/metrics.jsonl")
HOST_REMOTE = os.environ.get("OF_HOST_REMOTE",
                             "apollo:/home/netter/of-work/train/host.json")
PUSH_ON     = os.environ.get("OF_METRICS_PUSH", "1") != "0"
ROTATE_AT   = 8 * 1024 * 1024


# Diese Schluessel duerfen NICHT auf 4 Stellen gerundet werden: eine Lernrate
# von 3e-5 waere danach 0.0, ein Zeitstempel unbrauchbar.
_NO_ROUND = {"lr", "t"}


def _round(obj, nd=4):
    """Rundet Gleitkommazahlen rekursiv (Listen/Dicts/Tupel), damit die
    JSONL-Zeilen kompakt bleiben. Nicht-float-Werte bleiben unveraendert."""
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: (v if k in _NO_ROUND else _round(v, nd)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        t = [_round(v, nd) for v in obj]
        return t if isinstance(obj, list) else tuple(t)
    return obj


class MetricsLog:
    def __init__(self, path="logs/metrics.jsonl", label="", device="",
                 steps_total=None, push=True, push_every=15.0):
        self.path = path
        self.label = label
        self.device = str(device)
        self.steps_total = steps_total
        self.push = push and PUSH_ON
        self.push_every = push_every
        self.run = time.strftime("%Y%m%d-%H%M%S")
        self._last_push = 0.0
        self._inflight = False
        self._lock = threading.Lock()

        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        try:                                   # nicht unbegrenzt wachsen lassen
            if os.path.getsize(path) > ROTATE_AT:
                os.replace(path, path + ".prev")
        except OSError:
            pass

    def log(self, step, loss, acc=None, **extra):
        rec = {"run": self.run, "t": round(time.time(), 1), "kind": "step",
               "step": int(step), "loss": float(loss), "label": self.label,
               "device": self.device}
        if acc is not None:
            rec["acc"] = float(acc)
        if self.steps_total:
            rec["steps_total"] = int(self.steps_total)
        rec.update(extra)
        self._write(rec)
        self._maybe_push()

    def event(self, kind, **fields):
        """Schreibt eine Zeile fuer start/val/snap/end. Stoesst denselben
        gedrosselten Push an wie log()."""
        rec = {"run": self.run, "t": round(time.time(), 1), "kind": kind}
        rec.update(fields)
        self._write(rec)
        self._maybe_push()

    def push_now(self):
        """Erzwingt einen Push (nicht blockierend)."""
        self._maybe_push(force=True)

    def _write(self, rec):
        rec = _round(rec)
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass

    def _maybe_push(self, force=False):
        if not self.push:
            return
        now = time.time()
        with self._lock:
            if self._inflight or (not force and now - self._last_push < self.push_every):
                return
            self._inflight = True
            self._last_push = now
        threading.Thread(target=self._push, daemon=True).start()

    def _push(self):
        try:
            subprocess.run(
                ["rsync", "-q", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=5",
                 self.path, REMOTE],
                capture_output=True, timeout=30)
        except Exception:
            pass                                # Live-Seite ist Beiwerk, nie Blocker
        finally:
            with self._lock:
                self._inflight = False

    def close(self):
        self._maybe_push(force=True)
        for _ in range(60):                     # letzten Push noch abwarten
            with self._lock:
                if not self._inflight:
                    return
            time.sleep(0.5)
