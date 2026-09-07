"""
Aktionsübersetzung — zwischen echtem Spiel-Intent und den Netz-Köpfen.

Das Netz denkt in Zahlen an festen Plätzen (Kopf `atype` = 1, `target` = 3, …).
Das Spiel spricht in Intents (`{"type":"attack","targetID":null,"troops":5000}`).
Dieses Modul übersetzt beide Richtungen und garantiert per Test, dass
`decode(encode(intent)) == intent` (bis auf bewusst quantisierte Felder).

Zwei Sorten von Feldern:

  STATISCH   hängen nur am Intent selbst — Aktionstyp, Gebäudetyp, Emoji,
             Quick-Chat, Embargo-Richtung. Hier vollständig und ohne Engine testbar.

  KONTEXT    brauchen den Spielzustand im selben Tick:
               • magnitude  = troops/gold als Bruchteil → braucht den aktuellen Bestand
               • target     = Zeiger in die Gegnerliste → braucht deren Reihenfolge
               • own_ref    = Zeiger in die eigenen Einheiten/Angriffe
               • tile       = Kachelindex ↔ (grob, fein) → braucht Kartenmaße
             Dieser Kontext kommt beim Training aus obs.ts (dieselbe Reihenfolge,
             dieselben Maße), beim Spielen aus der Live-Engine. Hier gegen einen
             synthetischen Kontext getestet.

Ein Intent, den das Netz gar nicht entscheidet (`mark_disconnected`, Verwaltung),
ist bewusst NICHT abgebildet.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from enum import IntEnum


# ─────────────────────────────────────────── Aktionstypen

class A(IntEnum):
    NO_OP = 0
    ATTACK = 1
    BUILD_UNIT = 2
    BOAT = 3
    ALLIANCE_REQUEST = 4
    SPAWN = 5
    DONATE_TROOPS = 6
    UPGRADE_STRUCTURE = 7
    ALLIANCE_EXTENSION = 8
    EMOJI = 9
    MOVE_WARSHIP = 10
    CANCEL_ATTACK = 11
    BREAK_ALLIANCE = 12
    TARGET_PLAYER = 13
    QUICK_CHAT = 14
    CANCEL_BOAT = 15
    ALLIANCE_REJECT = 16
    EMBARGO = 17
    DONATE_GOLD = 18
    EMBARGO_ALL = 19
    DELETE_UNIT = 20


# Intent-"type"-String ↔ Aktionstyp. Genau die Strings aus den echten Records.
TYPE_TO_A = {
    "attack": A.ATTACK, "build_unit": A.BUILD_UNIT, "boat": A.BOAT,
    "allianceRequest": A.ALLIANCE_REQUEST, "spawn": A.SPAWN,
    "donate_troops": A.DONATE_TROOPS, "upgrade_structure": A.UPGRADE_STRUCTURE,
    "allianceExtension": A.ALLIANCE_EXTENSION, "emoji": A.EMOJI,
    "move_warship": A.MOVE_WARSHIP, "cancel_attack": A.CANCEL_ATTACK,
    "breakAlliance": A.BREAK_ALLIANCE, "targetPlayer": A.TARGET_PLAYER,
    "quick_chat": A.QUICK_CHAT, "cancel_boat": A.CANCEL_BOAT,
    "allianceReject": A.ALLIANCE_REJECT, "embargo": A.EMBARGO,
    "donate_gold": A.DONATE_GOLD, "embargo_all": A.EMBARGO_ALL,
    "delete_unit": A.DELETE_UNIT,
}
A_TO_TYPE = {v: k for k, v in TYPE_TO_A.items()}

# Nicht abgebildet, weil keine Spielentscheidung:
IGNORED_TYPES = {"mark_disconnected"}


# ─────────────────────────────────────────── Gebäude/Einheiten

# Baubare Typen, mit exakten Anzeige-Strings aus den Records. Reihenfolge fix —
# der Index IST das Label des unit_type-Kopfes.
UNIT_TYPES = [
    "City", "Port", "Factory", "Defense Post", "Missile Silo",
    "SAM Launcher", "Warship", "Atom Bomb", "Hydrogen Bomb", "MIRV",
]
UNIT_TO_IDX = {u: i for i, u in enumerate(UNIT_TYPES)}
NUM_UNIT_TYPES = len(UNIT_TYPES)


# ─────────────────────────────────────────── Quick-Chat

# Feste, sortierte Schlüsselliste, damit der Index stabil bleibt. Enthält alle
# in den Records gesehenen Schlüssel; unbekannte Schlüssel → UNKNOWN_QUICKCHAT.
QUICKCHAT_KEYS = sorted([
    "attack.attack", "attack.build_warships", "attack.focus", "attack.mirv",
    "defend.build_posts", "defend.defend",
    "greet.bye", "greet.gg", "greet.good_luck", "greet.have_fun", "greet.hello",
    "greet.nice_to_meet", "greet.ruining_games", "greet.same_team",
    "greet.thanks", "greet.trust_me", "greet.well_played",
    "help.gold", "help.no_attack", "help.sorry_attack", "help.trade_partners",
    "help.troops", "help.troops_frontlines",
    "misc.build_closer", "misc.coastline", "misc.strategy", "misc.team_up",
    "warnings.mirv_soon", "warnings.number1_warning", "warnings.stalemate",
    "warnings.stop_trading", "warnings.stop_trading_all", "warnings.strong",
    "warnings.weak",
])
QUICKCHAT_TO_IDX = {k: i for i, k in enumerate(QUICKCHAT_KEYS)}
UNKNOWN_QUICKCHAT = len(QUICKCHAT_KEYS)   # Reserveindex für neue Schlüssel
NUM_QUICKCHAT = len(QUICKCHAT_KEYS) + 1

NUM_EMOJI = 64          # Emoji-IDs 0..59 gesehen, Puffer bis 63


# ─────────────────────────────────────────── Ziel-Zeiger

MAX_OPP = 24            # Gegner-Topliste aus obs.encodeVec(topK=24)
TGT_NEUTRAL = MAX_OPP       # Angriff auf herrenloses Land (targetID = null)
TGT_ALL = MAX_OPP + 1       # embargo_all / Emoji an alle
NUM_TARGET = MAX_OPP + 2
TGT_UNRESOLVED = -1     # Empfänger nicht in der Topliste → Label maskieren


# ─────────────────────────────────────────── Eigene Referenz (Einheiten/Angriffe)

MAX_OWN_REF = 128
OWN_UNRESOLVED = -1


# ─────────────────────────────────────────── Menge (Bruchteil)

# Bruchteil-Klassen für Truppen/Gold. Der Kontext liefert den Nenner.
MAG_FRACTIONS = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.55, 0.70, 0.85, 1.0]
NUM_MAG = len(MAG_FRACTIONS)


def mag_encode(value: float, denom: float) -> int:
    """Absoluter Wert + Nenner → nächste Bruchteil-Klasse."""
    if denom <= 0:
        return 0
    frac = max(0.0, min(1.0, value / denom))
    # nächstgelegene Kante
    i = bisect.bisect_left(MAG_FRACTIONS, frac)
    if i == 0:
        return 0
    if i >= NUM_MAG:
        return NUM_MAG - 1
    return i if abs(MAG_FRACTIONS[i] - frac) < abs(MAG_FRACTIONS[i - 1] - frac) else i - 1


def mag_decode(idx: int, denom: float) -> float:
    return MAG_FRACTIONS[idx] * denom


# ─────────────────────────────────────────── Kachel ↔ (grob, fein)

# Zweistufiger Kachel-Zeiger. Grob wählt eine Zelle im COARSE-Raster, fein wählt
# darin eine Zelle im FINE-Raster über dem Vollausschnitt dieser Grobzelle.
COARSE_W, COARSE_H = 180, 90
FINE_W, FINE_H = 8, 8
NUM_COARSE = COARSE_W * COARSE_H
NUM_FINE = FINE_W * FINE_H

# Intern ein flaches globales Raster; grob/fein sind nur dessen Zerlegung.
# Ein flaches Raster rechnet sauber hin und zurück — das verschachtelte tat es
# an Zellgrenzen nicht (geschachtelte Ganzzahldivision driftet um ±1).
GW, GH = COARSE_W * FINE_W, COARSE_H * FINE_H     # 1440 × 720


def tile_encode(tile: int, W: int, H: int) -> tuple[int, int]:
    x, y = tile % W, tile // W
    gx = min(GW - 1, x * GW // W)
    gy = min(GH - 1, y * GH // H)
    coarse = (gy // FINE_H) * COARSE_W + (gx // FINE_W)
    fine = (gy % FINE_H) * FINE_W + (gx % FINE_W)
    return coarse, fine


def tile_decode(coarse: int, fine: int, W: int, H: int) -> int:
    """Repräsentative Kachel (Mitte der globalen Zelle). Beim echten Spielen
    sucht die Engine von hier aus die beste legale Kachel; hier reicht die Mitte.
    Solange das globale Raster nicht feiner als die Karte ist (GW≤W, GH≤H), gilt
    tile_encode(tile_decode(c,f)) == (c,f)."""
    cx, cy = coarse % COARSE_W, coarse // COARSE_W
    fx, fy = fine % FINE_W, fine // FINE_W
    gx, gy = cx * FINE_W + fx, cy * FINE_H + fy
    x = min(W - 1, (gx * W + W // 2) // GW)
    y = min(H - 1, (gy * H + H // 2) // GH)
    return y * W + x


# ─────────────────────────────────────────── Kontext & Aktion

@dataclass
class Context:
    """Spielzustand des handelnden Spielers im Entscheidungstick.
    Kommt aus obs.ts (Training) bzw. der Live-Engine (Spielen)."""
    map_w: int = 0
    map_h: int = 0
    troops: float = 0.0
    gold: float = 0.0
    opp_ids: list[str] = field(default_factory=list)     # Reihenfolge wie in obs
    own_unit_ids: list[int] = field(default_factory=list)
    own_attack_ids: list[str] = field(default_factory=list)

    def tgt_index(self, client_id) -> int:
        if client_id is None:
            return TGT_NEUTRAL
        try:
            return self.opp_ids.index(client_id)
        except ValueError:
            return TGT_UNRESOLVED

    def tgt_client(self, idx: int):
        if idx == TGT_NEUTRAL:
            return None
        if 0 <= idx < len(self.opp_ids):
            return self.opp_ids[idx]
        return None

    def own_index(self, unit_id: int, attacks=False) -> int:
        lst = self.own_attack_ids if attacks else self.own_unit_ids
        try:
            return lst.index(unit_id)
        except ValueError:
            return OWN_UNRESOLVED

    def own_id(self, idx: int, attacks=False):
        lst = self.own_attack_ids if attacks else self.own_unit_ids
        return lst[idx] if 0 <= idx < len(lst) else None


@dataclass
class Action:
    """Ein Zug in Kopf-Form. Nicht benutzte Köpfe bleiben None — die BC-Verlust-
    maske supervidiert genau die Köpfe, die der Typ tatsächlich verwendet."""
    atype: int = A.NO_OP
    target: int | None = None
    coarse: int | None = None
    fine: int | None = None
    unit_type: int | None = None
    own_ref: int | None = None
    magnitude: int | None = None
    emoji: int | None = None
    quickchat: int | None = None
    embargo_start: int | None = None       # 1 = start, 0 = stop
    rocket_up: int | None = None
    amount: int | None = None              # Stapelmenge (build/upgrade), 1..50

    def active_heads(self) -> list[str]:
        return [h for h in HEAD_SCHEMA.get(A(self.atype), ())
                if getattr(self, h) is not None]


# Welche Köpfe jeder Aktionstyp verwendet (die BC-Maske).
HEAD_SCHEMA: dict[A, tuple[str, ...]] = {
    A.NO_OP: (),
    A.ATTACK: ("target", "magnitude"),
    A.BUILD_UNIT: ("unit_type", "coarse", "fine", "rocket_up", "amount"),
    A.BOAT: ("coarse", "fine", "magnitude"),
    A.ALLIANCE_REQUEST: ("target",),
    A.SPAWN: ("coarse", "fine"),
    A.DONATE_TROOPS: ("target", "magnitude"),
    A.UPGRADE_STRUCTURE: ("unit_type", "own_ref", "amount"),
    A.ALLIANCE_EXTENSION: ("target",),
    A.EMOJI: ("target", "emoji"),
    A.MOVE_WARSHIP: ("own_ref", "coarse", "fine"),
    A.CANCEL_ATTACK: ("own_ref",),
    A.BREAK_ALLIANCE: ("target",),
    A.TARGET_PLAYER: ("target",),
    A.QUICK_CHAT: ("target", "quickchat"),
    A.CANCEL_BOAT: ("own_ref",),
    A.ALLIANCE_REJECT: ("target",),
    A.EMBARGO: ("target", "embargo_start"),
    A.DONATE_GOLD: ("target", "magnitude"),
    A.EMBARGO_ALL: ("embargo_start",),
    A.DELETE_UNIT: ("own_ref",),
}


# ─────────────────────────────────────────── encode: Intent → Action

class Unresolved(Exception):
    """Ein Kontext-Feld ließ sich nicht auflösen (Empfänger außerhalb der
    Topliste, Einheit nicht in der Liste). Das Label wird dann maskiert."""


def encode(intent: dict, ctx: Context) -> Action:
    ty = intent.get("type")
    if ty in IGNORED_TYPES or ty not in TYPE_TO_A:
        return Action(atype=A.NO_OP)
    a = A(TYPE_TO_A[ty])
    act = Action(atype=a)

    if a == A.ATTACK:
        act.target = ctx.tgt_index(intent.get("targetID"))
        act.magnitude = mag_encode(float(intent.get("troops") or 0), ctx.troops)

    elif a == A.BOAT:
        act.coarse, act.fine = tile_encode(int(intent["dst"]), ctx.map_w, ctx.map_h)
        act.magnitude = mag_encode(float(intent.get("troops") or 0), ctx.troops)

    elif a == A.BUILD_UNIT:
        act.unit_type = UNIT_TO_IDX.get(intent.get("unit"))
        if act.unit_type is None:
            return Action(atype=A.NO_OP)
        act.coarse, act.fine = tile_encode(int(intent["tile"]), ctx.map_w, ctx.map_h)
        act.rocket_up = 1 if intent.get("rocketDirectionUp") else 0
        act.amount = int(intent.get("amount") or 1)

    elif a == A.UPGRADE_STRUCTURE:
        act.unit_type = UNIT_TO_IDX.get(intent.get("unit"))
        act.own_ref = ctx.own_index(int(intent["unitId"]))
        act.amount = int(intent.get("amount") or 1)

    elif a == A.SPAWN:
        act.coarse, act.fine = tile_encode(int(intent["tile"]), ctx.map_w, ctx.map_h)

    elif a == A.MOVE_WARSHIP:
        ids = intent.get("unitIds") or []
        act.own_ref = ctx.own_index(int(ids[0])) if ids else OWN_UNRESOLVED
        act.coarse, act.fine = tile_encode(int(intent["tile"]), ctx.map_w, ctx.map_h)

    elif a in (A.DONATE_TROOPS,):
        act.target = ctx.tgt_index(intent.get("recipient"))
        act.magnitude = mag_encode(float(intent.get("troops") or 0), ctx.troops)

    elif a == A.DONATE_GOLD:
        act.target = ctx.tgt_index(intent.get("recipient"))
        act.magnitude = mag_encode(float(intent.get("gold") or 0), ctx.gold)

    elif a in (A.ALLIANCE_REQUEST, A.ALLIANCE_EXTENSION, A.BREAK_ALLIANCE):
        act.target = ctx.tgt_index(intent.get("recipient"))

    elif a == A.ALLIANCE_REJECT:
        act.target = ctx.tgt_index(intent.get("requestor"))

    elif a == A.TARGET_PLAYER:
        act.target = ctx.tgt_index(intent.get("target"))

    elif a == A.EMOJI:
        rec = intent.get("recipient")
        # recipient kann AllPlayers (Zahl 1 im Wire, hier als Sonderziel) sein.
        act.target = TGT_ALL if isinstance(rec, int) else ctx.tgt_index(rec)
        act.emoji = int(intent.get("emoji") or 0)

    elif a == A.QUICK_CHAT:
        act.target = ctx.tgt_index(intent.get("recipient"))
        act.quickchat = QUICKCHAT_TO_IDX.get(intent.get("quickChatKey"), UNKNOWN_QUICKCHAT)

    elif a == A.EMBARGO:
        act.target = ctx.tgt_index(intent.get("targetID"))
        act.embargo_start = 1 if intent.get("action") == "start" else 0

    elif a == A.EMBARGO_ALL:
        act.embargo_start = 1 if intent.get("action") == "start" else 0

    elif a == A.CANCEL_ATTACK:
        act.own_ref = ctx.own_index(intent.get("attackID"), attacks=True)

    elif a == A.CANCEL_BOAT:
        act.own_ref = ctx.own_index(int(intent["unitID"]))

    elif a == A.DELETE_UNIT:
        act.own_ref = ctx.own_index(int(intent["unitId"]))

    return act


# ─────────────────────────────────────────── decode: Action → Intent

def decode(act: Action, ctx: Context) -> dict:
    a = A(act.atype)
    if a == A.NO_OP:
        return {"type": "no_op"}
    ty = A_TO_TYPE[a]
    out: dict = {"type": ty}

    if a == A.ATTACK:
        out["targetID"] = ctx.tgt_client(act.target)
        out["troops"] = mag_decode(act.magnitude, ctx.troops)

    elif a == A.BOAT:
        out["dst"] = tile_decode(act.coarse, act.fine, ctx.map_w, ctx.map_h)
        out["troops"] = mag_decode(act.magnitude, ctx.troops)

    elif a == A.BUILD_UNIT:
        out["unit"] = UNIT_TYPES[act.unit_type]
        out["tile"] = tile_decode(act.coarse, act.fine, ctx.map_w, ctx.map_h)
        if act.rocket_up:
            out["rocketDirectionUp"] = True
        if act.amount and act.amount > 1:
            out["amount"] = act.amount

    elif a == A.UPGRADE_STRUCTURE:
        out["unit"] = UNIT_TYPES[act.unit_type]
        out["unitId"] = ctx.own_id(act.own_ref)
        if act.amount and act.amount > 1:
            out["amount"] = act.amount

    elif a == A.SPAWN:
        out["tile"] = tile_decode(act.coarse, act.fine, ctx.map_w, ctx.map_h)

    elif a == A.MOVE_WARSHIP:
        out["unitIds"] = [ctx.own_id(act.own_ref)]
        out["tile"] = tile_decode(act.coarse, act.fine, ctx.map_w, ctx.map_h)

    elif a == A.DONATE_TROOPS:
        out["recipient"] = ctx.tgt_client(act.target)
        out["troops"] = mag_decode(act.magnitude, ctx.troops)

    elif a == A.DONATE_GOLD:
        out["recipient"] = ctx.tgt_client(act.target)
        out["gold"] = mag_decode(act.magnitude, ctx.gold)

    elif a in (A.ALLIANCE_REQUEST, A.ALLIANCE_EXTENSION, A.BREAK_ALLIANCE):
        out["recipient"] = ctx.tgt_client(act.target)

    elif a == A.ALLIANCE_REJECT:
        out["requestor"] = ctx.tgt_client(act.target)

    elif a == A.TARGET_PLAYER:
        out["target"] = ctx.tgt_client(act.target)

    elif a == A.EMOJI:
        out["recipient"] = "AllPlayers" if act.target == TGT_ALL else ctx.tgt_client(act.target)
        out["emoji"] = act.emoji

    elif a == A.QUICK_CHAT:
        out["recipient"] = ctx.tgt_client(act.target)
        out["quickChatKey"] = (QUICKCHAT_KEYS[act.quickchat]
                               if act.quickchat < len(QUICKCHAT_KEYS) else None)

    elif a == A.EMBARGO:
        out["targetID"] = ctx.tgt_client(act.target)
        out["action"] = "start" if act.embargo_start else "stop"

    elif a == A.EMBARGO_ALL:
        out["action"] = "start" if act.embargo_start else "stop"

    elif a == A.CANCEL_ATTACK:
        out["attackID"] = ctx.own_id(act.own_ref, attacks=True)

    elif a == A.CANCEL_BOAT:
        out["unitID"] = ctx.own_id(act.own_ref)

    elif a == A.DELETE_UNIT:
        out["unitId"] = ctx.own_id(act.own_ref)

    return out


# Gesamt-Kopfgrößen — für den Netzbau.
HEAD_SIZES = {
    "atype": len(A), "target": NUM_TARGET,
    "coarse": NUM_COARSE, "fine": NUM_FINE,
    "unit_type": NUM_UNIT_TYPES, "own_ref": MAX_OWN_REF,
    "magnitude": NUM_MAG, "emoji": NUM_EMOJI, "quickchat": NUM_QUICKCHAT,
    "embargo_start": 2, "rocket_up": 2, "amount": 51,
}
