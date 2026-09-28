"""BC-Trainer für die Ausgabe des Materialisierers v2 (Format 2).

Liest die Partien direkt aus den Ausgabeordnern (of-mat2-out/s0…s7), trainiert
das Netz hinter netze.NetzAdapter (Standard: env/net.py unverändert) und misst
auf einem festen Eval-Set aus Val-Partien (reader.is_val).

Echter Lauf auf arch (erst nach ausdrücklichem Go des Users):
  cd ~/mat-dev/trainer-dev && setsid nohup ~/mat-dev/torchenv/bin/python trainer/train.py \\
      --daten ~/of-mat2-out --ckpt checkpoints/bc2.pt --epochen 3 \\
      --reputation ~/projects/openfront-ai/data/reputation.json \\
      > logs/train.log 2>&1 < /dev/null &
Fortsetzen: derselbe Befehl, der Checkpoint wird gefunden. Anhalten: SIGTERM an
den Hauptprozess, er speichert und beendet sich.

Neues Netz D0 (netz_d0.py): zusätzlich --netz d0 --batch 256, optional
--raum-verlust lset, --schicht wasserstoff=8,mirv=16,schiff_bewegen=4,
--lr-plan cosinus --warmup 1000, --kompilieren, --channels-last.

Rauchtest ohne Board:
  ... trainer/train.py --daten ~/of-mat2-out --ckpt /tmp/rauch.pt --schritte 400 \\
      --eval-start --eval-alle 0 --kein-push --frisch
"""
from __future__ import annotations

import argparse
import math
import os
import random
import signal
import sys
import time
from collections import Counter

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import torch  # noqa: E402
from torch.utils.data import DataLoader  # noqa: E402

import daten as D  # noqa: E402  (setzt sys.path auf env/ und materializer/py/)
import bewertung as B  # noqa: E402
import netze as N  # noqa: E402
import tabellen as T  # noqa: E402
import verlust as V  # noqa: E402
import actions as AC  # noqa: E402
import featurize as FZ  # noqa: E402
import reader as R  # noqa: E402
import zusatz_felder as ZF  # noqa: E402

FORMAT = "bc2-trainer-1"


def argumente(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_argument_group("Daten")
    g.add_argument("--daten", nargs="+", default=["~/of-mat2-out"])
    g.add_argument("--workers", type=int, default=4, help="Lader-Prozesse (arch: höchstens 4)")
    g.add_argument("--puffer", type=int, default=32768, help="Mischpuffer gesamt (Samples, komprimiert)")
    g.add_argument("--offen", type=int, default=6, help="gleichzeitig offene Partien je Worker")
    g.add_argument("--prefetch", type=int, default=4)
    g.add_argument("--reputation", default=None)
    g.add_argument("--max-partien", type=int, default=0, help="nur die ersten N Partien (Test)")
    g.add_argument("--partien-liste", default=None,
                   help="Datei mit Spiel-IDs (waehle_partien.py): Training UND Validierung nur daraus")
    g.add_argument("--spieler", default=None,
                   help="TSV gid<TAB>sid[,sid]<TAB>train|val: nur Samples dieser Spieler (Train und Eval). "
                        "Partien und Val-Split kommen aus der Datei statt aus R.is_val; braucht --schritte")
    g.add_argument("--deckel", type=int, default=0,
                   help="höchstens N Samples je Partie: jedes Sample wird mit p = min(1, N/Samples) "
                        "behalten, über die ganze Partie verteilt, je Epoche neu gezogen")
    g.add_argument("--zusatz", default=None,
                   help="Ordner mit <gid>.zusatz.zst (zusatz/src/lauf.ts): die neuen Beobachtungs-"
                        "felder je Gegnerplatz werden hinten an die opp-Merkmale gehängt. Ein alter "
                        "Checkpoint wird beim Laden mit Nullspalten erweitert und bleibt gültig")
    g.add_argument("--raum-label", choices=("res", "klick"), default="res")
    g.add_argument("--ohne-ziel-d0", action="store_true", help="Boot/Nukes ohne target-Label")
    g.add_argument("--schicht", default="",
                   help="seltene Gruppen vervielfachen, z. B. wasserstoff=8,mirv=16,schiff_bewegen=4")
    g.add_argument("--schicht-anheben", action="store_true",
                   help="Kopien behalten volles Gewicht (Standard: w/k, Lernziel bleibt gleich)")
    g = ap.add_argument_group("Training")
    g.add_argument("--netz", default="alt", help='"alt" (env/net.py), "d0" (netz_d0.py) oder modul:funktion')
    g.add_argument("--kompilieren", nargs="?", const="alles", default=None, choices=("alles", "kodierer"),
                   help="torch.compile über NetzAdapter.kompiliere (ohne Wert: alles). ACHTUNG: am 12.09. "
                        "auf arch (torch 2.14, RTX 5080) unzuverlässig — entweder CUDA illegal memory "
                        "access im ersten Rückwärtslauf oder still falsche Gradienten (Norm bis 7e6 statt "
                        "20, Verlust fällt kaum). Eager ist geprüft und nur ~25 % langsamer")
    g.add_argument("--cudnn-benchmark", action="store_true",
                   help="cuDNN-Autotuning der Faltungen einschalten. Standard aus: zusammen mit "
                        "--kompilieren stürzte der erste Rückwärtslauf am 12.09. reproduzierbar mit "
                        "CUDA illegal memory access ab (torch 2.14, RTX 5080). Gemessen ist es ohne "
                        "Autotuning sogar schneller (2980 statt 2800 Samples/s)")
    g.add_argument("--channels-last", action="store_true")
    g.add_argument("--lr-plan", choices=("konstant", "cosinus"), default="konstant")
    g.add_argument("--warmup", type=int, default=0, help="Schritte linearer Anstieg der LR")
    g.add_argument("--lr-min", type=float, default=1e-5, help="Endwert beim Cosinus-Plan")
    g.add_argument("--geraet", default="cuda" if torch.cuda.is_available() else "cpu")
    g.add_argument("--batch", type=int, default=128)
    g.add_argument("--lr", type=float, default=3e-4)
    g.add_argument("--wd", type=float, default=0.01)
    g.add_argument("--clip", type=float, default=0.0, help="Gradienten-Norm kappen (0 = aus)")
    g.add_argument("--epochen", type=int, default=3)
    g.add_argument("--schritte", type=int, default=0, help="Gesamtschritte, 0 = nur Epochen")
    g.add_argument("--max-min", type=float, default=0.0, help="nach so vielen Minuten sauber stoppen")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--init", default=None, help="nur Gewichte aus diesem Checkpoint laden")
    g = ap.add_argument_group("Verlust")
    g.add_argument("--atype-verlust", choices=("faktor", "flach"), default="faktor")
    g.add_argument("--adv-beta", type=float, default=1.5)
    g.add_argument("--value-w", type=float, default=1.0)
    g.add_argument("--ohne-maske", action="store_true")
    g.add_argument("--sigma-faktor", type=float, default=0.5, help="σ = faktor · R")
    g.add_argument("--weich-anteil", type=float, default=0.5)
    g.add_argument("--fein", action="store_true", help="Fein-Kopf mitlernen (D0: aus)")
    g.add_argument("--raum-verlust", choices=("weich", "lset"), default="weich",
                   help="Kachel-Verlust: weiches Ziel σ=R/2 (D0) oder L-set (Entwurf §6)")
    g = ap.add_argument_group("Checkpoints, Eval, Board")
    g.add_argument("--ckpt", default="checkpoints/bc2.pt")
    g.add_argument("--ckpt-alle", type=int, default=500)
    g.add_argument("--snapshot-alle", type=int, default=20000)
    g.add_argument("--frisch", action="store_true", help="vorhandenen Checkpoint ignorieren")
    g.add_argument("--bis-planende", action="store_true",
                   help="Epochen wiederholen, bis die Schritte des LR-Plans erreicht sind. Für wachsende "
                        "Daten: frühe Epochen sind kleiner, der Plan zielt auf die volle Liste; ohne "
                        "den Schalter endete der Lauf, bevor der Cosinus unten ankommt")
    g.add_argument("--log-alle", type=int, default=50)
    g.add_argument("--eval-alle", type=int, default=2000, help="0 = nur am Ende")
    g.add_argument("--eval-start", action="store_true")
    g.add_argument("--eval-nur", action="store_true")
    g.add_argument("--val-aus", default=None,
                   help="Val-Partien aus diesem Checkpoint übernehmen (gleiches Eval-Set für Vergleiche)")
    g.add_argument("--eval-allg", type=int, default=8000)
    g.add_argument("--eval-je-partie", type=int, default=32)
    g.add_argument("--kein-board", action="store_true", help="keine Metriken (auch nicht lokal)")
    g.add_argument("--kein-push", action="store_true", help="Metriken nur lokal, nicht nach apollo")
    g.add_argument("--metrik-pfad", default="logs/metrics.jsonl")
    return ap.parse_args(argv)


# ---------------------------------------------------------------- Checkpoint
def speichern(pfad, zustand):
    d = os.path.dirname(pfad)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = pfad + ".tmp"
    torch.save(zustand, tmp)
    os.replace(tmp, pfad)             # atomar, nie ein halber Checkpoint


def lr_faktor(schritt: int, a, gesamt: int) -> float:
    """Faktor auf --lr: linearer Warmup, danach konstant oder Cosinus bis --lr-min."""
    f = (schritt + 1) / a.warmup if a.warmup and schritt < a.warmup else 1.0
    if a.lr_plan == "cosinus":
        T = max(1, gesamt - a.warmup)
        s = min(max(schritt - a.warmup, 0), T)
        unten = min(1.0, a.lr_min / a.lr)
        f *= unten + (1 - unten) * 0.5 * (1 + math.cos(math.pi * s / T))
    return f


class Schritt:
    """Ein Trainingsschritt. Auf CUDA ohne Sync: fused AdamW bekommt found_inf (wie beim
    GradScaler) und überspringt das Update auf der GPU, wenn der Verlust oder ein
    Gradient nicht endlich ist. Verlustsumme und Zahl übersprungener Schritte bleiben
    Tensoren und werden nur an Log-Schritten gelesen. tests/netzbench.py misst genau
    diese Klasse."""

    def __init__(self, adapter, opt, vopt, clip, dev, ohne_sync=None):
        self.ad, self.opt, self.vopt, self.clip = adapter, opt, vopt, clip
        self.params = [p for p in adapter.modul.parameters() if p.requires_grad]
        self.cuda = str(dev).startswith("cuda")
        self.ohne_sync = self.cuda if ohne_sync is None else ohne_sync   # braucht fused AdamW
        # 0-dim wie im GradScaler: fused AdamW zieht found_inf von den 0-dim Schrittzählern ab
        self.found = torch.zeros((), device=dev)
        self.eins = torch.ones((), device=dev)
        self.summe = torch.zeros((), device=dev)
        self.nicht_endlich = torch.zeros((), device=dev)

    def __call__(self, g, lr):
        with torch.autocast(device_type="cuda" if self.cuda else "cpu", dtype=torch.bfloat16, enabled=self.cuda):
            out = self.ad.vorwaerts(g)
        loss, logs, info = V.berechne(out, g, self.vopt)
        self.opt.zero_grad(set_to_none=True)
        loss.backward()
        gn = torch.nn.utils.clip_grad_norm_(self.params, self.clip) if self.clip > 0 else None
        for pg in self.opt.param_groups:
            pg["lr"] = lr
        lo = loss.detach()
        endlich = torch.isfinite(lo)
        if self.ohne_sync:
            self.found.zero_()
            grads = [p.grad for p in self.params if p.grad is not None]
            torch._amp_foreach_non_finite_check_and_unscale_(grads, self.found, self.eins)
            self.found.copy_(torch.maximum(self.found, (~endlich).float()))
            self.opt.found_inf = self.found
            self.opt.step()
            self.nicht_endlich += self.found
        elif bool(endlich):
            self.opt.step()
        else:
            self.nicht_endlich += 1
        self.summe += torch.where(endlich, lo, torch.zeros_like(lo))
        return out, logs, info, gn

    def lies(self):
        """(Verlustsumme, übersprungene Schritte) seit dem letzten Lesen; ein Sync."""
        s, n = torch.stack([self.summe, self.nicht_endlich]).tolist()
        self.summe.zero_()
        return s, n


def lade_modell(net, sd: dict) -> list[str]:
    """Gewichte laden. Ist die Eingabebreite gewachsen (neue Zusatzfelder hinten), werden
    die neuen Spalten mit 0 aufgefüllt: das Netz rechnet zunächst exakt wie vorher, und der
    alte Checkpoint bleibt gültig."""
    eigen, angepasst = net.state_dict(), []
    for k, v in list(sd.items()):
        e = eigen.get(k)
        if e is None or e.shape == v.shape:
            continue
        if e.dim() == 2 and v.dim() == 2 and e.shape[0] == v.shape[0] and e.shape[1] > v.shape[1]:
            neu = e.new_zeros(e.shape)
            neu[:, :v.shape[1]] = v
            sd[k] = neu
            angepasst.append(f"{k}: {tuple(v.shape)} → {tuple(e.shape)}")
    net.load_state_dict(sd)
    return angepasst


def optimierer(net, a, dev):
    """AdamW; auf CUDA fused (nötig für found_inf ohne Sync)."""
    return torch.optim.AdamW(net.parameters(), lr=a.lr, weight_decay=a.wd,
                             fused=str(dev).startswith("cuda"))


def schicht_von(text: str) -> dict:
    out = {}
    for teil in filter(None, (x.strip() for x in text.split(","))):
        name, _, k = teil.partition("=")
        out[T.GID[name.strip()]] = int(k)
    return out


def snap_name(pfad, zusatz):
    return pfad[:-3] + f"_{zusatz}.pt" if pfad.endswith(".pt") else f"{pfad}_{zusatz}"


# ---------------------------------------------------------------- Board
class Board:
    """Meldet wie bc_fit.py: metrics.MetricsLog (JSONL + rsync nach apollo),
    trainstats.ActivationProbe und HostProbe. Jeder Fehler schaltet nur das Board ab."""

    def __init__(self, a, adapter, start_felder):
        self.ml = self.probe = self.host = None
        self.an = False
        if a.kein_board:
            return
        try:
            import metrics as MET
            import trainstats as TS
            self.TS = TS
            push = not a.kein_push
            self.ml = MET.MetricsLog(path=a.metrik_pfad, label="mat2 " + adapter.name,
                                     device=a.geraet, steps_total=a.schritte or None, push=push)
            self.ml.event("start", **start_felder)
            self.ml.push_now()
            self.probe = TS.ActivationProbe(adapter.modul)
            self.host = TS.HostProbe(path=os.path.join(os.path.dirname(a.metrik_pfad) or "logs", "host.json"),
                                     push=push,
                                     project_dir=os.path.dirname(os.path.abspath(a.metrik_pfad)) or ".").start()
            self.an = True
        except Exception as e:
            print(f"[warn] Board aus (Start): {e}", flush=True)
            self.an = False

    def _sicher(self, f, *x, **k):
        if not self.an:
            return
        try:
            f(*x, **k)
        except Exception as e:
            print(f"[warn] Board aus: {e}", flush=True)
            self.an = False

    def arm(self, g):
        if self.an and self.probe is not None:
            self._sicher(lambda: (self.probe.arm(opp_mask=g["opp_mask"]), self.probe.note_map_in(g["map"])))

    def schritt(self, step, loss, **felder):
        def f():
            act = self.probe.collect() if self.probe is not None else {}
            self.ml.log(step, loss, act=act, **felder)
        self._sicher(f)

    def ereignis(self, art, **felder):
        self._sicher(lambda: (self.ml.event(art, **felder), self.ml.push_now()))

    def ende(self, **felder):
        if not self.an:
            return
        self.ereignis("end", **felder)
        for f in (lambda: self.probe.remove(), lambda: self.host.stop(), lambda: self.ml.close()):
            try:
                f()
            except Exception:
                pass


# ---------------------------------------------------------------- Main
def main(argv=None):
    a = argumente(argv)
    torch.manual_seed(a.seed)
    dev = a.geraet
    amp = dev.startswith("cuda")
    if amp:
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = a.cudnn_benchmark
    lopt = D.LaderOpt(a.raum_label, not a.ohne_ziel_d0, a.reputation, lset=a.raum_verlust == "lset",
                      schicht=schicht_von(a.schicht), schicht_erhalten=not a.schicht_anheben, deckel=a.deckel,
                      saat=a.seed, zusatz=a.zusatz)
    # Das Eval-Set ist fest und geschichtet: kein Deckel, keine Vervielfachung seltener Typen.
    lopt_eval = D.LaderOpt(a.raum_label, not a.ohne_ziel_d0, a.reputation, lset=a.raum_verlust == "lset",
                           zusatz=a.zusatz)
    liste = None
    if a.partien_liste:
        with open(os.path.expanduser(a.partien_liste)) as f:
            liste = {z.strip() for z in f if z.strip() and not z.startswith("#")}
        print(f"[liste] {len(liste)} Partien aus {a.partien_liste}", flush=True)
    sp_val: set = set()
    if a.spieler:
        sp: dict = {}
        with open(os.path.expanduser(a.spieler)) as f:
            for z in f:
                t = z.rstrip("\n").split("\t")
                if z.startswith("#") or len(t) < 3:
                    continue
                sp[t[0]] = frozenset(int(x) for x in t[1].split(","))
                if t[2] == "val":
                    sp_val.add(t[0])
        lopt.spieler = lopt_eval.spieler = sp
        liste = set(sp) if liste is None else liste & set(sp)
        if not a.schritte:
            sys.exit("[abbruch] --spieler braucht --schritte (Planlänge aus ganzen Partien wäre falsch)")
        print(f"[spieler] {len(sp)} Partien, davon {len(sp_val)} val, aus {a.spieler}: "
              f"nur Samples dieser Spieler", flush=True)
    ist_val = (lambda g: g in sp_val) if a.spieler else R.is_val
    vopt = V.VerlustOpt(atype=a.atype_verlust, adv_beta=a.adv_beta, value_w=a.value_w,
                        maske=not a.ohne_maske, sigma_faktor=a.sigma_faktor,
                        weich_anteil=a.weich_anteil, fein=a.fein, raum=a.raum_verlust)

    if a.netz == "d1" and not a.zusatz:
        sys.exit("[abbruch] --netz d1 braucht --zusatz: die Zusatzfelder sind fester Bestandteil des Netzes")
    netz_opt = ({"opp_dim": FZ.OPP_DIM + ZF.DIM_OPP, "own_dim": FZ.OWN_DIM + ZF.DIM_GLOBAL}
                if a.zusatz else {})
    if a.zusatz:
        print(f"[zusatz] {a.zusatz}: {ZF.DIM_OPP} Felder je Gegnerplatz und {ZF.DIM_GLOBAL} globale; "
              f"opp {FZ.OPP_DIM}+{ZF.DIM_OPP}, own {FZ.OWN_DIM}+{ZF.DIM_GLOBAL}", flush=True)
    adapter = N.baue_netz(a.netz, **netz_opt)
    net = adapter.modul.to(dev)
    adapter.speicherformat(a.channels_last)
    print(f"[netz] {adapter.name}: {sum(p.numel() for p in net.parameters()) / 1e6:.2f} Mio Parameter", flush=True)
    opt = optimierer(net, a, dev)
    gstep, epoch, epoch_liste, erledigt, val_gids, plan_gesamt = 0, 0, None, set(), None, None
    zaehler: Counter = Counter()
    if os.path.exists(a.ckpt) and not a.frisch:
        z = torch.load(a.ckpt, map_location="cpu", weights_only=True)
        if z.get("netz") != adapter.name:
            sys.exit(f"[abbruch] Checkpoint gehört zu Netz {z.get('netz')!r}, gewählt ist {adapter.name!r}")
        for zeile in lade_modell(net, z["model"]):
            print(f"[gewichte] erweitert, neue Spalten 0: {zeile}", flush=True)
        opt.load_state_dict(z["opt"])
        gstep, epoch = z["gstep"], z["epoch"]
        epoch_liste, erledigt, val_gids = z["epoch_liste"], set(z["erledigt"]), z["val_gids"]
        zaehler.update(z.get("zaehler", {}))
        plan_gesamt = z.get("plan_gesamt")
        print(f"[fortsetzen] {a.ckpt}: Epoche {epoch}, Schritt {gstep}, "
              f"{len(erledigt)} Partien dieser Epoche schon gelesen", flush=True)
    elif a.init:
        z = torch.load(a.init, map_location="cpu", weights_only=True)
        for zeile in lade_modell(net, z["model"]):
            print(f"[gewichte] erweitert, neue Spalten 0: {zeile}", flush=True)
        print(f"[init] Gewichte aus {a.init}", flush=True)
    if a.kompilieren:
        adapter.kompiliere(a.kompilieren)

    voll: dict = {}                      # alle Partien der Liste im Pool, ohne Blick auf Zusatzdateien

    def partien_jetzt():
        alle = D.finde_partien(a.daten, nur=liste)
        if a.max_partien:
            alle = dict(sorted(alle.items())[:a.max_partien])
        voll.clear()
        voll.update(alle)
        if a.zusatz:
            # Fester Bestandteil: nur Partien mit fertiger Zusatzdatei. Der Einsammler legt
            # Dateien atomar ab (rsync bzw. prüfen und umbenennen); .tmp/.part zählen nie.
            da = {n[:-len(".zusatz.zst")] for n in os.listdir(os.path.expanduser(a.zusatz))
                  if n.endswith(".zusatz.zst")}
            alle = {g: v for g, v in alle.items() if g in da}
            print(f"[zusatz] {len(alle)} von {len(voll)} Partien mit Zusatzdatei, "
                  f"{len(voll) - len(alle)} übersprungen", flush=True)
        return alle

    t = time.time()
    alle = partien_jetzt()
    if a.val_aus:
        val_gids = torch.load(a.val_aus, map_location="cpu", weights_only=True)["val_gids"]
        print(f"[val] {len(val_gids)} Val-Partien aus {a.val_aus}", flush=True)
    if val_gids is None:
        val_gids = sorted(g for g in alle if ist_val(g))
    train_gids = sorted(g for g in alle if not ist_val(g))
    print(f"[daten] {len(alle)} fertige Partien, {sum(n for _, n in alle.values())} Samples: "
          f"{len(train_gids)} train / {len(val_gids)} val ({time.time() - t:.1f}s)", flush=True)
    if not train_gids:
        sys.exit("[abbruch] keine fertigen Trainingspartien")
    if plan_gesamt is None:              # Länge für den LR-Plan, beim Fortsetzen aus dem Checkpoint
        # Aus der VOLLEN Liste (alle Partien im Pool), nicht aus dem Zusatz-Bestand beim Start:
        # der wächst während des Laufs, der Plan zielt aber auf die endgültige Menge.
        tr_voll = [g for g in voll if not ist_val(g)]
        je_epoche = sum(min(voll[g][1], a.deckel) if a.deckel else voll[g][1] for g in tr_voll)
        plan_gesamt = a.schritte or max(1, a.epochen * je_epoche // a.batch)
        print(f"[lr] Planlänge aus {len(tr_voll)} Trainingspartien der vollen Liste, "
              f"{je_epoche} Samples je Epoche", flush=True)
    print(f"[lr] {a.lr_plan}, Warmup {a.warmup}, Planlänge {plan_gesamt} Schritte", flush=True)

    t = time.time()
    es = D.waehle_eval([(g, alle[g][0]) for g in val_gids if g in alle], lopt_eval, max(1, a.workers),
                       k_allg=a.eval_je_partie, allg_max=a.eval_allg, seed=a.seed)
    print(f"[eval-set] {es['partien']} Val-Partien: allg {len(es['allg'])}, räumlich {len(es['raum'])} "
          f"(Schichten soll/ist {es['schichten']}) in {time.time() - t:.0f}s", flush=True)

    letzte_eval = {"schritt": None}

    def eval_jetzt():
        letzte_eval["schritt"] = gstep
        r = B.bewerte(adapter, es, dev, vopt, batch=a.batch, amp=amp)
        B.drucke(r, gstep)
        board.ereignis("val", ep=epoch, step=gstep, vloss=r["vloss"], vacc=r["vacc"], vha=r["vha"],
                       batches=math.ceil(r["n_allg"] / a.batch), secs=r["secs"], raum=r["raum"],
                       p_handeln=r["p_handeln"], maske_verletzt=r["maske_verletzt"])
        return r

    params = sum(p.numel() for p in net.parameters())
    board = Board(a, adapter, dict(
        label="mat2 " + adapter.name, device=dev, epochs=a.epochen, batch=a.batch, lr=a.lr,
        games_total=len(alle), games_train=len(train_gids), games_val=len(val_gids),
        adv_beta=a.adv_beta, value_w=a.value_w, params=int(params), heads=D.HEADS,
        head_sizes=adapter.kopf_groessen, core_heads=list(B.CORE_HEADS),
        atype_names=[x.name for x in AC.A], layers=adapter.board_schichten(),
        netz=adapter.name, lr_plan=a.lr_plan, warmup=a.warmup, schicht=a.schicht,
        partien_liste=a.partien_liste, deckel=a.deckel,
        bytes_per_sample=18 * 90 * 180, verlust=vars(vopt), tiles={"raum_label": a.raum_label,
                                                                   "maske": not a.ohne_maske,
                                                                   "fein": a.fein}))
    if a.eval_nur:
        eval_jetzt()
        board.ende(ep=epoch, step=gstep, samples=0, secs=0)
        return

    stop = {"grund": None}

    def halt(sig, _f):
        stop["grund"] = signal.Signals(sig).name
        print(f"[signal] {stop['grund']}: speichere nach diesem Schritt", flush=True)
    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)

    def zustand():
        return {"format": FORMAT, "netz": adapter.name, "model": net.state_dict(), "opt": opt.state_dict(),
                "gstep": gstep, "epoch": epoch, "epoch_liste": epoch_liste, "erledigt": sorted(erledigt),
                "val_gids": val_gids, "args": vars(a), "zaehler": dict(zaehler), "plan_gesamt": plan_gesamt}

    if a.eval_start:
        eval_jetzt()

    t_lauf = time.time()
    samples_gesamt = 0
    nicht_endlich = 0
    schritt = Schritt(adapter, opt, vopt, a.clip, dev)
    fertig_lauf = False
    while (epoch < a.epochen or a.bis_planende) and not fertig_lauf:
        if a.bis_planende and gstep >= plan_gesamt:
            break
        if epoch_liste is None:
            alle = partien_jetzt()               # neue fertige Partien kommen hier dazu
            val_set = set(val_gids)
            epoch_liste = sorted(g for g in alle if not ist_val(g) and g not in val_set)
            random.Random(a.seed * 1000 + epoch).shuffle(epoch_liste)
            erledigt = set()
            n_liste = sum(1 for g in voll if not ist_val(g))
            print(f"[epoche {epoch}] {len(epoch_liste)} von {n_liste} Trainingspartien der Liste dabei"
                  f"{' (mit fertiger Zusatzdatei)' if a.zusatz else ''}", flush=True)
            board.ereignis("epoche", ep=epoch, step=gstep, partien=len(epoch_liste), partien_liste=n_liste)
            if not epoch_liste:
                sys.exit("[abbruch] keine Trainingspartien für diese Epoche")
        rest = [(g, alle[g][0]) for g in epoch_liste if g not in erledigt and g in alle]
        lopt.epoche = epoch                 # Deckel: je Epoche eine andere Teilmenge je Partie
        print(f"[epoche {epoch}] {len(rest)} von {len(epoch_liste)} Partien offen"
              f"{f', Deckel {a.deckel}' if a.deckel else ''}", flush=True)
        ds = D.Strom(rest, a.batch, max(1, a.puffer // max(1, a.workers)), a.offen,
                     a.seed * 1000 + epoch + gstep, lopt)
        dl = DataLoader(ds, batch_size=None, num_workers=a.workers, pin_memory=amp,
                        prefetch_factor=a.prefetch if a.workers > 0 else None)
        it = iter(dl)
        fenster = {"t": time.time(), "n": 0, "loss": 0.0, "k": 0, "warten": 0.0}
        erster = None
        while True:
            tw = time.time()
            try:
                b = next(it)
            except StopIteration:
                break
            fenster["warten"] += time.time() - tw
            if erster is None:
                erster = time.time()
                print(f"[lader] erster Batch nach {erster - tw:.1f}s", flush=True)
            erledigt.update(b["fertig"])
            zaehler.update(b["zaehler"])
            if b.get("leer"):
                continue
            g = D.auf_geraet(b, dev)
            n_b = int(b["lab"].shape[0])
            will_log = (gstep + 1) % a.log_alle == 0
            if will_log:
                board.arm(g)
            out, logs, info, gn_t = schritt(g, a.lr * lr_faktor(gstep, a, plan_gesamt))
            gstep += 1
            samples_gesamt += n_b
            fenster["n"] += n_b
            fenster["k"] += 1

            if gstep % a.log_alle == 0:
                if gn_t is not None:
                    gnorm = float(gn_t)
                else:
                    norms = [p.grad.detach().float().norm() for p in net.parameters() if p.grad is not None]
                    gnorm = float(torch.linalg.vector_norm(torch.stack(norms))) if norms else 0.0
                summe, ne = schritt.lies()
                fenster["loss"] = summe * fenster["k"] / max(1.0, fenster["k"] - (ne - nicht_endlich))
                if ne > nicht_endlich:
                    print(f"[warn] {ne - nicht_endlich:.0f} Schritte mit nicht endlichem Verlust/Gradienten "
                          f"übersprungen ({ne:.0f} bisher)", flush=True)
                    nicht_endlich = ne
                    if nicht_endlich > 50:
                        sys.exit("[abbruch] mehr als 50 nicht endliche Schritte")
                dt = time.time() - fenster["t"]
                kz = B.kopf_zaehler(out, g, info)
                ha = B.treffer(kz)
                hl = {h: float(v.detach()) for h, v in logs.items()
                      if h in ("ob", "welche", "atype", "coarse_nll", "value") or kz.get(h, (0, 0))[1] > 0}
                sps = fenster["n"] / max(dt, 1e-9)
                if kz["_maske"][2] > 0:
                    print(f"[warn] Zeiger-Überlauf: {kz['_maske'][2]:.0f} räumliche Zeilen ohne Logits "
                          f"in diesem Batch (--netz d0: zeiger_anteil)", flush=True)
                print(f"E{epoch} S{gstep:>7}  Verlust {fenster['loss'] / fenster['k']:6.3f}  "
                      f"(ob {hl.get('ob', 0):.3f} welche {hl.get('welche', 0):.3f} "
                      f"kachel {hl.get('coarse', float('nan')):.3f})  Kern {B.kern(ha):5.1%}  "
                      f"Kachel {ha.get('coarse', float('nan')):5.1%}  {sps:5.0f} Smp/s  "
                      f"warten {fenster['warten'] / max(dt, 1e-9):4.0%}", flush=True)
                if board.an:
                    he, hen, htop, hlg, ph, lh = B.kopf_statistik(out, g, info)
                    board.schritt(gstep, fenster["loss"] / fenster["k"], acc=B.kern(ha), ep=epoch,
                                  samples=samples_gesamt, games_done=len(erledigt),
                                  games_epoch=len(epoch_liste), hl=hl, ha=ha, he=he, hen=hen,
                                  htop=htop, hlg=hlg, pred=ph, lab=lh,
                                  adv=B.adv_statistik(info["adv"], g["win"], a.adv_beta),
                                  vmse=hl.get("value"), winfrac=float(g["win"].mean()),
                                  lr=opt.param_groups[0]["lr"], gnorm=gnorm, sps=sps, dt=dt,
                                  hact=hlg, warten=fenster["warten"] / max(dt, 1e-9))
                fenster = {"t": time.time(), "n": 0, "loss": 0.0, "k": 0, "warten": 0.0}
            if gstep % a.ckpt_alle == 0:
                speichern(a.ckpt, zustand())
            if a.snapshot_alle and gstep % a.snapshot_alle == 0:
                sn = snap_name(a.ckpt, f"s{gstep}")
                speichern(sn, zustand())
                board.ereignis("snap", ep=epoch, step=gstep, name=os.path.basename(sn),
                               mb=os.path.getsize(sn) / 2 ** 20)
            if a.eval_alle and gstep % a.eval_alle == 0:
                eval_jetzt()
                print(f"[lader] Zähler bisher: {dict(zaehler)}", flush=True)
                fenster = {"t": time.time(), "n": 0, "loss": 0.0, "k": 0, "warten": 0.0}
            zeit_um = a.max_min and time.time() - t_lauf > a.max_min * 60
            planende = a.bis_planende and gstep >= plan_gesamt
            if stop["grund"] or (a.schritte and gstep >= a.schritte) or zeit_um or planende:
                fertig_lauf = True
                break
        del it                                     # Worker beenden
        if fertig_lauf:
            speichern(a.ckpt, zustand())
            print(f"[stopp] Schritt {gstep}, Checkpoint {a.ckpt} "
                  f"({stop['grund'] or ('Zeit' if zeit_um else 'Schrittziel')})", flush=True)
            break
        sn = snap_name(a.ckpt, f"e{epoch}")
        epoch += 1
        epoch_liste, erledigt = None, set()
        speichern(a.ckpt, zustand())
        speichern(sn, zustand())
        print(f"[epoche fertig] Snapshot {sn}", flush=True)
        board.ereignis("snap", ep=epoch - 1, step=gstep, name=os.path.basename(sn),
                       mb=os.path.getsize(sn) / 2 ** 20)

    secs = time.time() - t_lauf
    print(f"[lauf] {gstep} Schritte gesamt, {samples_gesamt} Samples in {secs:.0f}s "
          f"({samples_gesamt / max(secs, 1e-9):.0f} Smp/s inkl. Anlauf und Eval)", flush=True)
    print(f"[lader] Zähler: {dict(zaehler)}", flush=True)
    if not stop["grund"] and letzte_eval["schritt"] != gstep:
        eval_jetzt()
    board.ende(ep=epoch, step=gstep, samples=samples_gesamt, secs=secs)
    print(f"fertig. Checkpoint: {a.ckpt}", flush=True)


if __name__ == "__main__":
    main()
