"""Spielregel für D0 und D1: aus einer Anfrage des Viewers wird eine Entscheidung.

D1 (netz_d1.py) braucht zusätzlich zusatz_b64/zusatz_sig (siehe zusatz_aus_anfrage) und
löst own_ref auf eine konkrete Einheit bzw. einen Angriff aus ctx auf.

Anfrage (JSON wie bei env/inf_server.py, erweitert):
  map_b64 | map_u8   Karte u8[18·90·180], base64 roh bzw. Liste (obsModel.ts, wie im Materialisierer)
  cells_b64          Zellfakten roh, 64'800 Byte: owner u16 LE ‖ own_frac u8 ‖ legal u8 (cellFacts.ts)
  own, opps, config  wie im Materialisierer (opps mit user/clan, config = info.config roh)
  sid, allies        eigene smallID, smallIDs der Verbündeten
  ctx                mapW, mapH, troops, gold, oppIds, ownUnitIds, ownAttackIds, oppSids
Antwort:
  intent             fertiger Intent. Bei räumlichen Aktionen steht als Kachel die Mitte der
                     besten Zelle; der Client ersetzt sie (Auflösung innerhalb der Zelle).
  kandidaten         bis zu 5 Zellen (Index im 90×180-Raster), beste zuerst. Der Client prüft
                     sie der Reihe nach mit der Engine (Top-5-Rückfall, Entwurf §4 Punkt 6).
  gruppe, einheit, zielSid, p_handeln, value, atype, wahl

Entscheidung (Entwurf §4/§6, kein Ziehen im BC-Einsatz):
  1. P(handeln) = 1 − p(Nichtstun). Liegt sie unter der Schwelle: Nichtstun.
     Die Schwelle kommt aus kalibriere_schwelle.py (menschennahe Handlungsrate).
  2. atype = argmax über die Aktionen ohne Nichtstun; dann unit_type, Zielspieler und die
     übrigen Köpfe, jeweils argmax und bedingt auf die schon gewählten Schritte.
  3. Räumlich: Zeiger-Logits mit dem legal-Bit der Gruppe maskiert; bei Nukes zusätzlich
     Zellen verbündeter Spieler (owner_major) gestrichen (grober Vorfilter, die exakte
     Regel wouldNukeBreakAlliance prüft der Client). MIRV hat keinen Zeiger: Zellen des Ziels.
Gerechnet wird wie im Trainer-Eval (bewertung.bewerte): Eingaben über daten.sammle und
daten.auf_geraet, Netz im train()-Modus unter no_grad, BatchNorm fest.
"""
from __future__ import annotations

import base64
import threading

import numpy as np
import torch

import daten as D
import tabellen as T
import verlust as V
import zusatz_felder as ZF
import actions as AC
import featurize as F
import reader as R

FEIN_MITTE = (AC.FINE_H // 2) * AC.FINE_W + AC.FINE_W // 2
NUKES = {"atom", "wasserstoff", "mirv"}

# ---------------------------------------------------------------- Zusatzfelder (D1)
# Die Anfrage trägt sie als zusatz_b64: opp ‖ global ‖ einheiten ‖ angriffe, float32 LE, roh,
# gerechnet von zusatz/src/felder.ts (ZusatzSpur, zusatzAnfrage) im Client. zusatz_sig ist
# der FNV-1a-Fingerabdruck der Feldlisten; er muss zu zusatz_felder.py passen.
ZUSATZ_BLOECKE = (F.MAX_OPP * ZF.DIM_OPP, ZF.DIM_GLOBAL, ZF.MAX_EINHEIT * ZF.DIM_EINHEIT,
                  ZF.MAX_ANGRIFF * ZF.DIM_ANGRIFF)


def _zusatz_sig() -> str:
    """Wie zusatzSig() in zusatz/src/felder.ts."""
    s = "|".join([",".join(ZF.NAMEN_OPP), ",".join(ZF.NAMEN_GLOBAL), ",".join(ZF.NAMEN_EINHEIT),
                  ",".join(ZF.NAMEN_ANGRIFF), f"{F.MAX_OPP},{ZF.MAX_EINHEIT},{ZF.MAX_ANGRIFF}"])
    h = 0x811C9DC5
    for ch in s:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


ZUSATZ_SIG = _zusatz_sig()


def zusatz_aus_anfrage(o: dict):
    """zusatz_b64 → (opp (24,30), global (27,), einheiten (128,7), angriffe (16,4)), normiert.

    Genau der Weg des Trainings: der Offline-Lauf speichert float16, der Lader liest float16
    und normiert mit zusatz_felder. Darum wird hier erst auf float16 gerundet (float32 →
    float16, round-to-nearest-even wie Float16Array.set in lauf.ts), dann normiert.
    Fehlen die Felder, bricht die Anfrage ab — nie mit Nullen auffüllen (wie im Lader)."""
    b64 = o.get("zusatz_b64")
    if not b64:
        raise ValueError("D1 braucht zusatz_b64 in der Anfrage (Zusatzfelder aus zusatz/src/felder.ts); "
                         "ohne sie wird nicht mit Nullen aufgefüllt")
    if o.get("zusatz_sig") != ZUSATZ_SIG:
        raise ValueError(f"zusatz_sig {o.get('zusatz_sig')!r} passt nicht zu zusatz_felder.py ({ZUSATZ_SIG}): "
                         "Client und Server kennen andere Feldlisten")
    roh = np.frombuffer(base64.b64decode(b64), "<f4")
    if roh.size != sum(ZUSATZ_BLOECKE):
        raise ValueError(f"zusatz_b64 hat {roh.size} Werte statt {sum(ZUSATZ_BLOECKE)}")
    with np.errstate(over="ignore"):              # Truppen > 65504 werden inf, wie in der Datei
        h = roh.astype(np.float16)
    a, b, c = np.cumsum(ZUSATZ_BLOECKE)[:3]
    return (ZF.normiere_opp(h[:a].reshape(F.MAX_OPP, ZF.DIM_OPP)), ZF.normiere_global(h[a:b]),
            ZF.normiere_einheit(h[b:c].reshape(ZF.MAX_EINHEIT, ZF.DIM_EINHEIT)),
            ZF.normiere_angriff(h[c:].reshape(ZF.MAX_ANGRIFF, ZF.DIM_ANGRIFF)))


def gruppe_von(atype: int, unit: int | None) -> str | None:
    if atype == AC.A.BUILD_UNIT:
        if unit is None or not (0 <= unit < AC.NUM_UNIT_TYPES):
            return None
        return T.gruppe({"type": "build_unit", "unit": AC.UNIT_TYPES[unit]})
    return {int(AC.A.BOAT): "boot", int(AC.A.MOVE_WARSHIP): "schiff_bewegen",
            int(AC.A.SPAWN): "spawn"}.get(int(atype))


def anfrage_zu_sample(o: dict, rep: dict, zusatz: bool = False) -> dict:
    """Anfrage → Sample-dict im Format von daten.PartieLeser (Blöcke roh).
    zusatz (D1): Zusatzfelder hinten an opp und own, dazu Einheiten und Angriffe — wie
    PartieLeser._sample mit --zusatz."""
    if o.get("map_b64") is not None:
        mb = base64.b64decode(o["map_b64"])
    else:
        mb = np.asarray(o["map_u8"], np.uint8).tobytes()
    if len(mb) != R.MAP_BYTES:
        raise ValueError(f"Karte hat {len(mb)} Byte statt {R.MAP_BYTES}")
    cb = None
    if o.get("cells_b64"):
        cb = base64.b64decode(o["cells_b64"])
        if len(cb) != R.CELL_BYTES:
            raise ValueError(f"Zellfakten haben {len(cb)} Byte statt {R.CELL_BYTES}")
    c = o["ctx"]
    opp_mat, mask = D.gegner_features(o.get("opps") or [], rep)
    lab = np.full(D.NH, D.IGNORE, np.int64)
    lab[D.HI["atype"]] = 0
    own_vec = np.asarray(F.featurize_own(o.get("own") or {}), np.float32)
    einheiten = angriffe = None
    if zusatz:
        zo, zg, einheiten, angriffe = zusatz_aus_anfrage(o)
        opp_mat = np.concatenate([opp_mat, zo], 1)
        own_vec = np.concatenate([own_vec, zg])
    return {"roh": True, "map": mb, "cell": cb,
            "own": own_vec, "opp": opp_mat, "mask": mask,
            "einheiten": einheiten, "angriffe": angriffe,
            "cfg": np.asarray(F.featurize_config(o.get("config") or {}), np.float32),
            "lab": lab, "w": 1.0, "wt": 1.0, "win": 0.0, "g": -1, "bit": 0, "xy": (0.0, 0.0),
            "wh": (float(c["mapW"]), float(c["mapH"])), "dst": 0, "lset": None, "id": ("anfrage", 0)}


def anfrage_zu_batch(o: dict, rep: dict, dev, zusatz: bool = False):
    s = anfrage_zu_sample(o, rep, zusatz)
    b = D.auf_geraet(D.sammle([s]), dev)
    b["zeiger_voll"] = True
    return b, s["cell"] is not None


def _setze(b, h, v):
    b["labels"][h][0] = int(v)


def _kein(res, grund=None):
    out = {**res, "intent": {"type": "no_op"}}
    if grund:
        out["grund"] = grund
    return out


class Wahl:
    """Wie aus den Logits eine Entscheidung wird. Standard ist das bisherige Argmax.

    Ziehen ist für den Aktionstyp gedacht: das Netz trifft ihn nur zu ~23 %, und ein
    Argmax spielt deshalb fast nur Angriffe (Arena bei Schritt 40'000: 515 Angriffe,
    kein einziges Bauwerk in 649 Anfragen). Die Schwelle für Nichtstun liegt davor und
    bleibt unberührt: erst wird entschieden OB gehandelt wird, dann WAS.
    """

    def __init__(self, ziehen: bool = False, temp: float = 1.0, top_k: int = 0,
                 koepfe: tuple = ("atype",), saat: int | None = None):
        self.ziehen = ziehen
        self.temp = temp
        self.top_k = top_k                 # 0 = alle Klassen
        self.koepfe = tuple(koepfe)        # welche Köpfe gezogen werden
        self.saat = saat

    def __repr__(self):
        return (f"Wahl(ziehen={self.ziehen}, temp={self.temp}, top_k={self.top_k}, "
                f"koepfe={','.join(self.koepfe)}, saat={self.saat})")


def gleichverteilt(saat: int, schluessel: str, kopf: str) -> float:
    """u in [0, 1) als reine Funktion von (Saat, Schlüssel, Kopf): blake2b, obere 53 Bit.

    Damit hängt der Zufall einer Entscheidung nur an Partie, Spieler, Tick und Kopf — nicht an
    der Reihenfolge, in der gleichzeitige Partien ihre Anfragen schicken (ein Server bedient
    viele Partien), und nicht an der torch-Version. Beide Seiten eines Paars bekommen dasselbe u."""
    import hashlib
    h = hashlib.blake2b(f"{int(saat)}|{schluessel}|{kopf}".encode(), digest_size=8).digest()
    return (int.from_bytes(h, "big") >> 11) / float(1 << 53)


def ziehe_invers(p: list[float], u: float) -> int:
    """Inverse Verteilungsfunktion: erster Index mit u < Σ p[..i]. p in der Reihenfolge der
    Top-k (absteigend) bzw. der Klassen. Monotone Kopplung: stimmen zwei Netze in der Rangfolge
    überein, wählt dasselbe u häufig denselben Rang (gemeinsame Zufallszahlen im Paar)."""
    s = 0.0
    for i, x in enumerate(p):
        s += x
        if u < s:
            return i
    return len(p) - 1                      # Rundung: Summe knapp unter 1


class Entscheider:
    def __init__(self, adapter, dev="cpu", rep: dict | None = None, wahl: Wahl | None = None):
        self.ad, self.dev, self.rep = adapter, dev, rep or {}
        # D1 liest die Zusatzfelder als feste Eingänge (netz_d1.py); D0 braucht und liest sie nicht
        self.zusatz = getattr(adapter, "name", "") == "d1"
        self.cuda = str(dev).startswith("cuda")
        # bf16 nur, wo das Training es ebenfalls nutzt. Der GPU-Server (inf_gpu.py) rechnet in fp32,
        # weil die Schwelle für P(handeln) auf fp32 kalibriert ist (bf16 verschob sie, 11.09.).
        self.autocast_an = self.cuda
        self._z_vor = None                 # vorab im Stapel gerechnete Kodierung (inf_gpu.py)
        self.lock = threading.Lock()
        self.wahl = wahl or Wahl()
        self.gen = None
        self._schluessel = None            # wahl_schluessel der laufenden Anfrage (unter self.lock)
        if self.wahl.saat is not None:
            self.gen = torch.Generator(device="cpu").manual_seed(int(self.wahl.saat))

    def _waehle(self, logits, kopf: str, offset: int = 0) -> int:
        """Argmax oder Ziehen aus softmax(logits/temp), auf Wunsch nur unter den Top k."""
        return self._waehle_lp(logits, kopf, offset)[0]

    def _waehle_lp(self, logits, kopf: str, offset: int = 0) -> tuple[int, float]:
        """Wie _waehle, dazu log μ(Wahl): die Log-Wahrscheinlichkeit unter der Verteilung, aus
        der tatsächlich gezogen wurde (Verhaltenspolitik fürs RL). Argmax ist deterministisch,
        also 0. Ziehen: log softmax(logits/temp) — mit Top-k über die k Besten renormiert.

        Gekoppelt (Saat gesetzt und die Anfrage trägt wahl_schluessel, arena.ts): u aus
        gleichverteilt(saat, schluessel, kopf), gezogen per inverser Verteilungsfunktion. Sonst
        wie bisher torch.multinomial mit dem Server-Generator (Reihenfolge der Anfragen zählt)."""
        w = self.wahl
        if not w.ziehen or kopf not in w.koepfe:
            return int(logits.argmax()) + offset, 0.0
        lg = logits.detach().float().cpu() / max(1e-3, w.temp)
        gekoppelt = w.saat is not None and self._schluessel is not None
        if 0 < w.top_k < lg.numel():
            werte, idx = lg.topk(w.top_k)
            p = torch.softmax(werte.double(), 0)
            if gekoppelt:
                j = ziehe_invers(p.tolist(), gleichverteilt(w.saat, self._schluessel, kopf))
            else:
                j = int(torch.multinomial(torch.softmax(werte, 0), 1, generator=self.gen))
            return int(idx[j]) + offset, float(torch.log_softmax(werte, 0)[j])
        if gekoppelt:
            j = ziehe_invers(torch.softmax(lg.double(), 0).tolist(),
                             gleichverteilt(w.saat, self._schluessel, kopf))
        else:
            j = int(torch.multinomial(torch.softmax(lg, 0), 1, generator=self.gen))
        return j + offset, float(torch.log_softmax(lg, 0)[j])

    def _autocast(self):
        return torch.autocast(device_type="cuda" if self.cuda else "cpu", dtype=torch.bfloat16,
                              enabled=self.autocast_an)

    def _koepfe(self, b):
        net = self.ad.modul
        if self._z_vor is not None:        # Stapelbetrieb: Kodierung liegt schon vor
            z = self._z_vor
            return lambda zeiger=False: net.koepfe_aus(b, z, zeiger)
        net.train()
        for mod in net.modules():
            if isinstance(mod, torch.nn.modules.batchnorm._BatchNorm):
                mod.eval()
        z = net.kodiere(b, self.ad._karte(b))
        return lambda zeiger=False: net.koepfe_aus(b, z, zeiger)

    @torch.no_grad()
    def vorwaerts_vorgabe(self, o: dict, labels: dict, g: int, bit: int, dst: int):
        """Alle Köpfe mit vorgegebenen Labels (für den Vergleich mit dem Trainer-Eval)."""
        b, _ = anfrage_zu_batch(o, self.rep, self.dev, self.zusatz)
        for h, v in labels.items():
            _setze(b, h, v)
        b["g"][0], b["bit"][0], b["dst"][0] = int(g), int(bit), int(dst)
        with self._autocast():
            return self._koepfe(b)(True), b

    @torch.no_grad()
    def entscheide(self, o: dict, schwelle: float, vorgabe: dict | None = None, top: int = 5,
                   allianz_filter: bool = True) -> dict:
        with self.lock:
            k = o.get("wahl_schluessel")
            self._schluessel = str(k) if k is not None else None
            try:
                return self._entscheide(o, schwelle, vorgabe or {}, top, allianz_filter)
            finally:
                self._schluessel = None

    def _entscheide(self, o, schwelle, v, top, allianz_filter, vor=None):
        b, hat_zellen = vor if vor is not None else anfrage_zu_batch(o, self.rep, self.dev, self.zusatz)
        with self._autocast():
            kp = self._koepfe(b)
            out = kp()
            la = out["atype"].float()[0]
            p_act = float(1.0 - torch.softmax(la, 0)[0])
            # Log-Wahrscheinlichkeiten je Kopf (Trajektorien-Recorder, RL):
            #   logp_v  unter der Verhaltenspolitik, also der Verteilung, aus der wirklich gewählt
            #           wurde (Ziehen mit Temperatur/Top-k; Argmax und die Schwelle: 0)
            #   logp_n  unter dem Netz bei Temperatur 1, so wie der Verlust sie rechnet
            #           (verlust.berechne: "ob" = log P(handeln) bzw. log p(Nichtstun),
            #           "atype" = log p(a | handeln), übrige Köpfe log_softmax, Zellen maskiert)
            lp_v, lp_n = {}, {}
            res = {"p_handeln": p_act, "value": float(out["value"].float()[0]), "atype": "NO_OP",
                   "logp_v": lp_v, "logp_n": lp_n}
            if "atype" not in v and p_act < schwelle:
                lp_n["ob"] = float(torch.log_softmax(la, 0)[0])
                return _kein(res)
            lp_n["ob"] = float(torch.logsumexp(la[1:], 0) - torch.logsumexp(la, 0))
            if "atype" in v:
                a, lp_v["atype"] = int(v["atype"]), 0.0
            else:
                a, lp_v["atype"] = self._waehle_lp(la[1:], "atype", 1)
            lp_n["atype"] = float(torch.log_softmax(la[1:], 0)[a - 1]) if a >= 1 else 0.0
            _setze(b, "atype", a)
            act = AC.Action(atype=a)
            schema = AC.HEAD_SCHEMA.get(AC.A(a), ())
            wahl = {"atype": a}
            if "unit_type" in schema:
                out = kp()
                lu = out["unit_type"].float()[0]
                if "unit_type" in v:
                    act.unit_type, lp_v["unit_type"] = int(v["unit_type"]), 0.0
                else:
                    act.unit_type, lp_v["unit_type"] = self._waehle_lp(lu, "unit_type")
                lp_n["unit_type"] = float(torch.log_softmax(lu, 0)[act.unit_type])
                _setze(b, "unit_type", act.unit_type)
                wahl["unit_type"] = act.unit_type
            gname = gruppe_von(a, act.unit_type)
            if gname is not None:
                b["g"][0], b["bit"][0] = T.GID[gname], T.BIT[gname]     # Client rechnet mit legalBit1
            ziel_sid = None
            if "target" in schema or gname in T.ZIEL_GRUPPEN:
                out = kp()
                lt = out["target"].float()[0]
                if "target" in v:
                    t, lp_v["target"] = int(v["target"]), 0.0
                else:
                    t, lp_v["target"] = self._waehle_lp(lt, "target")
                lp_n["target"] = float(torch.log_softmax(lt, 0)[t])
                _setze(b, "target", t)
                wahl["target"] = t
                if t >= 0:
                    if "target" in schema:
                        act.target = t
                    sids = o["ctx"].get("oppSids") or [x.get("id") for x in (o.get("opps") or [])]
                    if t < min(len(sids), AC.MAX_OPP) and sids[t] is not None:
                        ziel_sid = int(sids[t])
                    elif t == AC.TGT_NEUTRAL:
                        ziel_sid = 0
            if "dst" in v:
                ziel_sid = int(v["dst"])
            if gname in T.ZIEL_GRUPPEN:
                b["dst"][0] = ziel_sid if ziel_sid is not None else 0
            out = kp()
            for h in schema:
                if h in ("unit_type", "target", "coarse", "fine"):
                    continue
                lh = out[h].float()[0]
                # Ziehen nur für Köpfe aus --ziehen-koepfe; sonst gibt _waehle_lp Argmax und 0,0
                # zurück, also genau das bisherige Verhalten.
                if h in v:
                    gewaehlt, lpv = int(v[h]), 0.0
                else:
                    gewaehlt, lpv = self._waehle_lp(lh, h)
                setattr(act, h, gewaehlt)
                wahl[h] = getattr(act, h)
                lp_v[h] = lpv
                lp_n[h] = float(torch.log_softmax(lh, 0)[wahl[h]])
            res.update(atype=AC.A(a).name, gruppe=gname, zielSid=ziel_sid, wahl=wahl,
                       gezogen=self.wahl.ziehen and not v,
                       p_atype=float(torch.softmax(la, 0)[a]),
                       einheit=AC.UNIT_TYPES[act.unit_type] if act.unit_type is not None else None)
            if gname is not None:
                if not hat_zellen:
                    return _kein(res, "keine Zellfakten in der Anfrage")
                maske_verlust = V.zellmaske(b)[0]           # die Maske des Verlusts (nur legal-Bit)
                maske = maske_verlust.clone()
                owner = b["owner"][0].long()
                if allianz_filter and gname in NUKES and o.get("allies"):
                    maske &= ~torch.isin(owner, torch.tensor([int(x) for x in o["allies"]], device=owner.device))
                if T.R_KACHELN[gname] is None:              # MIRV: kein Zeiger, Zellen des Ziels
                    kand = ((owner == (ziel_sid if ziel_sid is not None else -1)) & maske).nonzero().squeeze(1)
                    kand = kand[:top].tolist()
                else:
                    out = kp(True)
                    n = int(maske.sum())
                    lg = out["coarse"].float()[0].masked_fill(~maske, float("-inf"))
                    kand = lg.topk(min(top, n)).indices.tolist() if n else []
                    # Top-5 der Logits, der Client nimmt die erste gültige: deterministisch (logp_v 0).
                    # logp_n je Kandidat unter der Verlust-Maske; welcher gilt, weiss erst der Client.
                    lsc = torch.log_softmax(out["coarse"].float()[0].masked_fill(~maske_verlust, float("-inf")), 0)
                    res["kand_logp_n"] = [float(lsc[c]) for c in kand]
                if not kand:
                    return _kein(res, "keine legale Zelle")
                act.coarse, act.fine = kand[0], FEIN_MITTE
                res["kandidaten"] = kand
        c = o["ctx"]
        ctx = AC.Context(map_w=c["mapW"], map_h=c["mapH"], troops=c["troops"], gold=c["gold"],
                         opp_ids=c.get("oppIds") or [], own_unit_ids=c.get("ownUnitIds") or [],
                         own_attack_ids=c.get("ownAttackIds") or [])
        if self.zusatz and act.own_ref is not None:
            # D1: own_ref zeigt auf einen Platz der Liste, die der Client mitgeschickt hat
            # (ownAttackIds bei cancel_attack, sonst ownUnitIds). Leerer Platz → nichts tun.
            ab = a == int(AC.A.CANCEL_ATTACK)
            ref_id = ctx.own_id(act.own_ref, attacks=ab)
            res["own_ref"] = {"platz": act.own_ref, "id": ref_id, "liste": "angriffe" if ab else "einheiten"}
            if ref_id is None:
                return _kein(res, "own_ref zeigt auf einen leeren Platz")
        res["intent"] = AC.decode(act, ctx)
        return res
