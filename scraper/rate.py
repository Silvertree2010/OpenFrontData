#!/usr/bin/env python3
"""
Spielstaerke aus den gesammelten Partien ableiten.

Wir brauchen keine fremde Rangliste: jeder Record nennt alle Teilnehmer, den
Sieger und je Spieler `killedAt` und `finalTiles`. Daraus laesst sich pro Partie
eine Platzierung bilden und daraus ueber viele Partien ein Elo.

Platzierung:  Sieger zuerst, dann wer laenger ueberlebt hat (killedAt), bei
Gleichstand mehr Endflaeche. Wer nie starb und nicht gewann, gilt als
ueberlebend bis Spielende.

Identitaet:   username + clanTag. Nicht garantiert eindeutig -- die API strippt
persistente IDs. Fuer eine Gewichtung reicht es; Spieler mit wenigen Partien
bekommen ohnehin kaum Vertrauen.

  rate.py            rechnet und schreibt Tabelle ratings
  rate.py --top 30   zeigt die Bestenliste
"""
import argparse, json, math, sqlite3, sys
from compression import zstd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DB = DATA / "index.sqlite"            # nur lesen
RDB = DATA / "ratings.sqlite"         # abgeleitet, eigene Datei
K_BASE = 32.0
START = 1500.0
MIN_GAMES = 5          # darunter keine Bestenlisten-Anzeige

SCHEMA = """
CREATE TABLE IF NOT EXISTS ratings (
  key TEXT PRIMARY KEY, username TEXT, clan_tag TEXT,
  elo REAL, games INTEGER, wins INTEGER, avg_place REAL
);
CREATE INDEX IF NOT EXISTS ix_ratings_elo ON ratings(elo);
"""


def key_of(username, clan):
    return f"{username or '?'}\x1f{clan or ''}"


def winners_of(blob):
    """clientIDs der Sieger. Bei Teamspielen listet winner die Mitglieder mit:
       ['team', 'Yellow', 'id1', 'id2', ...]; bei FFA ['player', 'id']."""
    if not blob:
        return set()
    try:
        w = json.loads(zstd.decompress(blob))["info"].get("winner")
    except Exception:
        return set()
    if not isinstance(w, list) or len(w) < 2:
        return set()
    if w[0] == "team":
        return set(x for x in w[2:] if isinstance(x, str))
    if w[0] == "player":
        return {w[1]}
    return set()          # 'nation' -> kein menschlicher Sieger


def placements(rows, winners):
    """rows: (client_id, username, clan, is_winner, killed_at, final_tiles).
    Gibt (key, rang) zurueck, Rang 0 = bester."""
    def sort_key(r):
        cid, _, _, _, killed, tiles = r
        return (0 if cid in winners else 1,
                -(killed if killed is not None else 10**9),
                -(tiles or 0))
    ordered = sorted(rows, key=sort_key)
    # Alle Sieger teilen sich Rang 0 -- sie sind ein Team, nicht Konkurrenten.
    return [(key_of(r[1], r[2]), 0 if r[0] in winners else i, r[0] in winners)
            for i, r in enumerate(ordered)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--min-games", type=int, default=MIN_GAMES)
    a = ap.parse_args()

    # Lesend auf den Index, schreibend nur in die eigene Datei: der Sammler
    # haelt den Schreiblock auf index.sqlite fast durchgehend.
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=120)
    con.execute("PRAGMA busy_timeout=120000")
    rcon = sqlite3.connect(RDB, timeout=120)
    rcon.executescript(SCHEMA)

    if a.top:
        rows = rcon.execute(
            "SELECT username, clan_tag, elo, games, wins, avg_place FROM ratings "
            "WHERE games>=? ORDER BY elo DESC LIMIT ?", (a.min_games, a.top)).fetchall()
        if not rows:
            print("Noch keine Bewertungen. Erst rate.py ohne --top laufen lassen.")
            return
        print(f"{'Spieler':<24}{'Clan':<7}{'Elo':>7}{'Spiele':>8}{'Siege':>7}{'Ø Platz':>9}")
        for u, c, e, g, w, ap_ in rows:
            print(f"{(u or '?')[:23]:<24}{(c or '')[:6]:<7}{e:>7.0f}{g:>8}{w:>7}{ap_:>9.1f}")
        return

    # Partien chronologisch, nur solche mit Metadaten und mind. 2 Spielern
    games = con.execute(
        "SELECT game_id, meta_json FROM games WHERE meta_at>0 AND num_players>=2 "
        "AND winner_kind IN ('player','team') ORDER BY start_ts").fetchall()
    print(f"{len(games):,} Partien mit Metadaten")

    elo, ngames, nwins, place_sum = {}, {}, {}, {}
    used = skipped = 0
    for gid, blob in games:
        winners = winners_of(blob)
        if not winners:
            skipped += 1
            continue
        rows = con.execute(
            "SELECT client_id, username, clan_tag, is_winner, killed_at, final_tiles "
            "FROM players WHERE game_id=?", (gid,)).fetchall()
        if len(rows) < 2:
            skipped += 1
            continue
        pl = placements(rows, winners)
        n = len(pl)
        used += 1
        # Jeder gegen jeden, aber mit gedaempftem K, damit eine 100er-Partie
        # nicht 99-mal so stark zaehlt wie ein 1v1.
        k = K_BASE / math.sqrt(n - 1)
        cur = {kk: elo.get(kk, START) for kk, _, _ in pl}
        delta = {kk: 0.0 for kk, _, _ in pl}
        for i in range(n):
            ki, ri, wi = pl[i]
            for j in range(i + 1, n):
                kj, rj, wj = pl[j]
                if ki == kj:
                    continue
                if wi and wj:
                    continue      # zwei Sieger sind Teamkollegen
                exp_i = 1.0 / (1.0 + 10 ** ((cur[kj] - cur[ki]) / 400.0))
                s_i = 1.0 if ri < rj else (0.0 if ri > rj else 0.5)
                delta[ki] += k * (s_i - exp_i)
                delta[kj] += k * ((1 - s_i) - (1 - exp_i))
        for kk, rank, _ in pl:
            elo[kk] = cur[kk] + delta[kk]
            ngames[kk] = ngames.get(kk, 0) + 1
            place_sum[kk] = place_sum.get(kk, 0.0) + (rank + 1)
            if rank == 0:
                nwins[kk] = nwins.get(kk, 0) + 1

    rcon.execute("DELETE FROM ratings")
    rcon.executemany(
        "INSERT INTO ratings VALUES (?,?,?,?,?,?,?)",
        [(kk, kk.split("\x1f")[0], kk.split("\x1f")[1] or None, e,
          ngames[kk], nwins.get(kk, 0), place_sum[kk] / ngames[kk])
         for kk, e in elo.items()])
    rcon.commit()

    tot = len(elo)
    exp = rcon.execute("SELECT COUNT(*) FROM ratings WHERE games>=?", (a.min_games,)).fetchone()[0]
    lo, hi = rcon.execute("SELECT MIN(elo), MAX(elo) FROM ratings WHERE games>=?", (a.min_games,)).fetchone()
    print(f"{used:,} Partien verwertet, {skipped:,} ohne verwertbaren Sieger")
    print(f"{tot:,} Spieler bewertet, davon {exp:,} mit >= {a.min_games} Partien")
    if lo is not None:
        print(f"Elo-Spanne (ab {a.min_games} Partien): {lo:.0f} .. {hi:.0f}")
    con.close(); rcon.close()


if __name__ == "__main__":
    main()
