"""
Statistik-Maschinerie fuers Live-Board, komplett getrennt von bc_fit.py, damit
der Eingriff dort minimal bleibt.

  - ActivationProbe: Forward-Hooks auf die Kernschichten, nur aktiv ("armiert")
    auf Log-Schritten -> kein Overhead in normalen Schritten.
  - head_stats: Entropie/Top-Anteil/Logit-Betrag je Kopf + Aktionstyp-Histogramme.
  - adv_stats: Verteilung der AWR-Gewichte (dieselbe Formel wie masked_loss).
  - grad_norm: globale Gradienten-Norm ohne Nebenwirkung.
  - HostProbe: Hintergrund-Thread, schreibt host.json und schiebt es per rsync.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

import torch
import torch.nn.functional as F

import actions as AC
from bc_train import ADV_BETA, ADV_WMIN, ADV_WMAX, IGNORE_INDEX
from net import head_masks_for

HEADS = list(AC.HEAD_SIZES.keys())
N_ATYPE = len(AC.A)


# ------------------------------------------------------------- Aktivierungen
class ActivationProbe:
    """Registriert Forward-Hooks auf die Kernschichten. Tut nichts, solange
    nicht armiert (probe.arm()) - keine Rechenkosten in normalen Schritten."""

    LAYERS = [
        ("map_stem", "map.stem"),
        ("map_down", "map.down"),
        ("opp", "opp.enc"),
        ("own", "own"),
        ("cfg", "cfg"),
        ("core1", "core.1"),
        ("core2", "core.3"),
    ]

    def __init__(self, net):
        self.net = net
        self._armed = False
        self._buf: dict[str, torch.Tensor] = {}
        self._opp_mask = None
        self._handles = []
        mods = dict(net.named_modules())
        for lid, name in self.LAYERS:
            mod = mods.get(name)
            if mod is None:
                continue
            h = mod.register_forward_hook(self._make_hook(lid))
            self._handles.append(h)

    def arm(self, opp_mask=None):
        self._armed = True
        self._buf = {}
        self._opp_mask = opp_mask

    def set_opp_mask(self, opp_mask):
        self._opp_mask = opp_mask

    def note_map_in(self, map_t):
        """Karten-Eingang hat keinen Hook (kein Modul) - hier manuell einspeisen."""
        if not self._armed:
            return
        self._reduce("map_in", map_t)

    def _make_hook(self, lid):
        def hook(module, inputs, output):
            if not self._armed:
                return
            self._reduce(lid, output)
        return hook

    def _reduce(self, lid, x):
        try:
            x = x.float()
            if lid == "opp":
                # (B,N,emb) maskiert ueber gueltige Gegner mitteln
                if self._opp_mask is not None:
                    w = self._opp_mask.float().unsqueeze(-1)          # (B,N,1)
                    num = (x.abs() * w).sum(dim=(0, 1))
                    den = w.sum(dim=(0, 1)).clamp(min=1e-6)
                    u = num / den                                     # (emb,)
                    # Anteil aktiver Einheiten: echtes Vorzeichen pruefen, NICHT
                    # den Betrag (abs>0 waere immer ~1 und damit nichtssagend).
                    a = ((x > 0).float() * w).sum() / (w.sum() * x.shape[-1]).clamp(min=1e-6)
                else:
                    u = x.abs().mean(dim=(0, 1))
                    a = (x > 0).float().mean()
            elif x.dim() == 4:
                # (B,C,H,W) CNN-Aktivierung
                u = x.abs().mean(dim=(0, 2, 3))
                a = (x > 0).float().mean()
            else:
                # (B,F)
                u = x.abs().mean(dim=0)
                a = (x > 0).float().mean()
            m = u.mean()
            q = self._quantile_indices(u)
            self._buf[lid] = (m, a, q)
        except Exception:
            pass   # Statistik darf das Training nie stoeren

    @staticmethod
    def _quantile_indices(u):
        n = u.numel()
        s, _ = torch.sort(u)
        if n >= 12:
            idx = torch.linspace(0, n - 1, 12, device=s.device).round().long()
            return s[idx]
        idx = torch.linspace(0, max(n - 1, 0), 12, device=s.device)
        lo = idx.floor().long().clamp(0, max(n - 1, 0))
        hi = idx.ceil().long().clamp(0, max(n - 1, 0))
        frac = idx - lo.float()
        return s[lo] * (1 - frac) + s[hi] * frac

    def collect(self):
        """dict {layer_id: {"m":float,"a":float,"q":[12 floats]}}. Genau EIN
        Sync-Punkt: alle Skalare in einen Tensor stapeln, dann ein .tolist()."""
        if not self._buf:
            self._armed = False
            self._buf = {}
            self._opp_mask = None
            return {}
        ids = list(self._buf.keys())
        ms = torch.stack([self._buf[i][0] for i in ids])
        as_ = torch.stack([self._buf[i][1] for i in ids])
        qs = torch.stack([self._buf[i][2].reshape(-1) for i in ids])   # (L,12)
        flat = torch.cat([ms, as_, qs.reshape(-1)]).tolist()
        L = len(ids)
        out = {}
        for k, lid in enumerate(ids):
            m = flat[k]
            a = flat[L + k]
            q = flat[2 * L + k * 12: 2 * L + k * 12 + 12]
            out[lid] = {"m": m, "a": a, "q": q}
        self._armed = False
        self._buf = {}
        self._opp_mask = None
        return out

    def remove(self):
        for h in self._handles:
            h.remove()
        self._handles = []


# ------------------------------------------------------------- Kopf-Stats
@torch.no_grad()
def head_stats(out, labels, atypes, heads=HEADS, head_sizes=None):
    """(he, hen, htop, hlg, pred_hist, lab_hist) gemaess Schema 1.2."""
    head_sizes = head_sizes or AC.HEAD_SIZES
    masks = head_masks_for(atypes)
    he, hen, htop, hlg = {}, {}, {}, {}

    def active_mask(h):
        if h == "atype":
            return torch.ones_like(atypes, dtype=torch.bool)
        m = masks[h].to(out[h].device)
        lab = labels[h]
        return m & (lab != IGNORE_INDEX)

    for h in heads:
        logits = out[h]
        am = active_mask(h)
        if not am.any():
            continue
        K = head_sizes.get(h, logits.shape[-1])
        sub = logits[am].float()
        sub = torch.nan_to_num(sub, nan=0.0, posinf=1e9, neginf=-1e9)
        logp = F.log_softmax(sub, dim=-1)
        p = logp.exp()
        ent = -(p * logp).sum(-1)
        ent = torch.nan_to_num(ent, nan=0.0)
        he[h] = float(ent.mean().item())
        denom = torch.log(torch.tensor(float(max(K, 2))))
        hen[h] = float((ent.mean() / denom.clamp(min=1e-6)).item())
        pred = sub.argmax(-1)
        counts = torch.bincount(pred, minlength=K)
        htop[h] = float(counts.max().item() / max(1, pred.numel()))
        # -1e9-Sentinel (maskierte Gegner-Slots im "target"-Kopf) darf den
        # Logit-Betrag nicht dominieren: als "nicht real" ausschliessen.
        finite = sub[torch.isfinite(sub) & (sub.abs() < 1e8)]
        hlg[h] = float(finite.abs().mean().item()) if finite.numel() else 0.0

    # Aktionstyp-Histogramme (pred/lab), Laenge N_ATYPE
    pred_at = out["atype"].argmax(-1)
    pred_hist = torch.bincount(pred_at, minlength=N_ATYPE)[:N_ATYPE].tolist()
    lab_hist = torch.bincount(labels["atype"], minlength=N_ATYPE)[:N_ATYPE].tolist()
    pred_hist = [int(v) for v in pred_hist]
    lab_hist = [int(v) for v in lab_hist]
    return he, hen, htop, hlg, pred_hist, lab_hist


# ------------------------------------------------------------- Advantage
@torch.no_grad()
def adv_stats(win: torch.Tensor) -> dict:
    """dict {mean,p10,p50,p90,max,clip} - dieselbe Formel wie masked_loss."""
    win = win.float()
    raw = torch.exp(ADV_BETA * (win - win.mean())) if ADV_BETA > 0 else torch.ones_like(win)
    clipped_frac = float(((raw <= ADV_WMIN) | (raw >= ADV_WMAX)).float().mean().item()) if ADV_BETA > 0 else 0.0
    w = raw.clamp(ADV_WMIN, ADV_WMAX) if ADV_BETA > 0 else raw
    w = w / w.mean().clamp(min=1e-6)
    qs = torch.quantile(w, torch.tensor([0.1, 0.5, 0.9], device=w.device))
    return {
        "mean": float(w.mean().item()),
        "p10": float(qs[0].item()),
        "p50": float(qs[1].item()),
        "p90": float(qs[2].item()),
        "max": float(w.max().item()),
        "clip": clipped_frac,
    }


# ------------------------------------------------------------- Gradienten
def grad_norm(params) -> float:
    """Globale L2-Norm der Gradienten, ohne die Gradienten zu veraendern."""
    n = torch.nn.utils.clip_grad_norm_(list(params), max_norm=float("inf"))
    return float(n.item()) if torch.is_tensor(n) else float(n)


# ------------------------------------------------------------- Host-Probe
HOST_REMOTE = os.environ.get("OF_HOST_REMOTE", "apollo:/home/netter/of-work/train/host.json")
HOST_PUSH = os.environ.get("OF_METRICS_PUSH", "1") != "0"


class HostProbe:
    """Hintergrund-Thread: schreibt alle `interval` Sekunden host.json und
    schiebt es per rsync. Jeder Fehler wird geschluckt, blockiert nie."""

    def __init__(self, path="logs/host.json", interval=20.0, push=True, project_dir=None):
        self.path = path
        self.interval = interval
        self.push = push and HOST_PUSH
        self.project_dir = project_dir or os.getcwd()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def _gpu(self):
        try:
            r = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8)
            if r.returncode != 0 or not r.stdout.strip():
                return None
            line = r.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                return None
            name, util, mu, mt, temp, pw, pcap = parts[:7]
            return {
                "name": name,
                "util": int(float(util)),
                "mem_used": int(float(mu)),
                "mem_total": int(float(mt)),
                "temp": int(float(temp)),
                "power": round(float(pw), 1),
                "power_cap": round(float(pcap), 1),
            }
        except Exception:
            return None

    def _ram(self):
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, _, v = line.partition(":")
                    info[k.strip()] = v.strip()
            total_kb = float(info["MemTotal"].split()[0])
            avail_kb = float(info.get("MemAvailable", "0").split()[0])
            used_gb = (total_kb - avail_kb) / 1024 / 1024
            total_gb = total_kb / 1024 / 1024
            return round(used_gb, 1), round(total_gb, 1)
        except Exception:
            return None, None

    def _tick(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        try:
            load = list(os.getloadavg())
        except Exception:
            load = None
        cores = os.cpu_count()
        ram_used, ram_total = self._ram()
        try:
            du = shutil.disk_usage(self.project_dir)
            disk_free_gb = round(du.free / 1024 / 1024 / 1024, 1)
        except Exception:
            disk_free_gb = None
        rec = {
            "t": round(time.time(), 1),
            "gpu": self._gpu(),
            "load": load,
            "cores": cores,
            "ram_used_gb": ram_used,
            "ram_total_gb": ram_total,
            "disk_free_gb": disk_free_gb,
            "train_alive": True,
        }
        import json
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, self.path)
        if self.push:
            try:
                subprocess.run(
                    ["rsync", "-q", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=5",
                     self.path, HOST_REMOTE],
                    capture_output=True, timeout=15)
            except Exception:
                pass
