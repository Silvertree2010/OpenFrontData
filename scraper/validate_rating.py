#!/usr/bin/env python3
"""
Taugt das Elo etwas?

Auf den aelteren 80% der Partien rechnen, auf den juengsten 20% pruefen:
wie oft ist der Sieger der hoechstbewertete Teilnehmer? Verglichen mit dem
Zufall (1/Teilnehmerzahl). Ohne diese Zahl ist jede Feinjustierung geraten.

  validate_rating.py [--damp sqrt|linear]
"""
import argparse, json, math, sqlite3
from compression import zstd
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "index.sqlite"
START, K_BASE = 1500.0, 32.0


def winners_of(blob):
    if not blob: return set()
    try: w = json.loads(zstd.decompress(blob))["info"].get("winner")
    except Exception: return set()
    if not isinstance(w, list) or len(w) < 2: return set()
    if w[0] == "team": return set(x for x in w[2:] if isinstance(x, str))
    if w[0] == "player": return {w[1]}
    return set()


def key_of(u, c): return f"{u or '?'}\x1f{c or ''}"


def run(damp):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=120)
    games = con.execute(
        "SELECT game_id, meta_json FROM games WHERE meta_at>0 AND num_players>=2 "
        "AND winner_kind IN ('player','team') ORDER BY start_ts").fetchall()
    split = int(len(games) * 0.8)
    train, test = games[:split], games[split:]

    elo, ngames = {}, {}

    def rows_of(gid):
        return con.execute(
            "SELECT client_id, username, clan_tag, is_winner, killed_at, final_tiles "
            "FROM players WHERE game_id=?", (gid,)).fetchall()

    def update(gid, blob):
        winners = winners_of(blob)
        if not winners: return
        rows = rows_of(gid)
        if len(rows) < 2: return
        ordered = sorted(rows, key=lambda r: (
            0 if r[0] in winners else 1,
            -(r[4] if r[4] is not None else 10**9), -(r[5] or 0)))
        pl = [(key_of(r[1], r[2]), 0 if r[0] in winners else i, r[0] in winners)
              for i, r in enumerate(ordered)]
        n = len(pl)
        k = K_BASE / (math.sqrt(n - 1) if damp == "sqrt" else (n - 1))
        cur = {kk: elo.get(kk, START) for kk, _, _ in pl}
        d = {kk: 0.0 for kk, _, _ in pl}
        for i in range(n):
            ki, ri, wi = pl[i]
            for j in range(i + 1, n):
                kj, rj, wj = pl[j]
                if ki == kj or (wi and wj): continue
                e = 1.0 / (1.0 + 10 ** ((cur[kj] - cur[ki]) / 400.0))
                s = 1.0 if ri < rj else (0.0 if ri > rj else 0.5)
                d[ki] += k * (s - e); d[kj] += k * ((1 - s) - (1 - e))
        for kk, _, _ in pl:
            elo[kk] = cur[kk] + d[kk]; ngames[kk] = ngames.get(kk, 0) + 1

    for gid, blob in train: update(gid, blob)

    hit = tot = 0
    chance = 0.0
    mrr = 0.0
    for gid, blob in test:
        winners = winners_of(blob)
        if not winners: continue
        rows = rows_of(gid)
        # nur Partien, in denen alle Teilnehmer schon bewertet sind
        keys = [(r[0], key_of(r[1], r[2])) for r in rows]
        if len(keys) < 3: continue
        known = [(cid, kk) for cid, kk in keys if ngames.get(kk, 0) >= 3]
        if len(known) < 3: continue
        ranked = sorted(known, key=lambda t: -elo.get(t[1], START))
        pos = next((i for i, (cid, _) in enumerate(ranked) if cid in winners), None)
        if pos is None: continue
        tot += 1
        if pos == 0: hit += 1
        chance += 1.0 / len(ranked)
        mrr += 1.0 / (pos + 1)

    if tot == 0:
        print(f"  {damp:<7} zu wenig Ueberschneidung fuer eine Aussage"); return
    print(f"  {damp:<7} Treffer {100*hit/tot:5.1f}%   Zufall {100*chance/tot:5.1f}%   "
          f"Faktor {(hit/tot)/(chance/tot):4.1f}x   MRR {mrr/tot:.3f}   n={tot}")


if __name__ == "__main__":
    a = argparse.ArgumentParser(); a.add_argument("--damp", default="both")
    x = a.parse_args()
    print("Vorhersage: ist der Sieger der hoechstbewertete Teilnehmer?")
    for d in (["sqrt", "linear"] if x.damp == "both" else [x.damp]): run(d)
