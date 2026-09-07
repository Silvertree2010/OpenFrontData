#!/usr/bin/env python3
"""
OpenFront-Datensammler.

Drei Stufen, jede fuer sich wiederaufnehmbar:

  list  /public/games?start&end        1000 IDs pro Anfrage   -> Tabelle games
  meta  /public/game/:id?turns=false   ~1 KB, Spieler+Sieger  -> Tabelle players
  full  /public/game/:id               ~233 KB, die Zuege     -> data/raw/<xx>/<id>.json.zst

Der Index (data/index.sqlite) ist die Abfrageschicht; die vollen Records liegen
zstd-komprimiert als eine Datei pro Spiel, damit ein Abbruch nie mehr kostet als
das eine Spiel und ein erneuter Lauf einfach ueberspringt, was schon da ist.

Beispiele:
  collect.py list --start 2026-08-25 --end 2026-09-05
  collect.py meta --limit 5000
  collect.py full --limit 1000 --where "mode='Free For All'"
  collect.py stats
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sqlite3
import sys
import time
from compression import zstd
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API = "https://api.openfront.io"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "index.sqlite"
RAW = DATA / "raw"
UA = "openfront-ai-research/0.1 (self-play RL research; contact via GitHub Silvertree2010)"

# Die Liste erlaubt hoechstens 2 Tage pro Anfrage und 1000 Treffer pro Seite.
MAX_WINDOW = timedelta(days=2)
PAGE = 1000

_stop = False


def _on_sigint(*_):
    global _stop
    _stop = True
    print("\n[!] Abbruch angefordert - beende nach der laufenden Anfrage.", flush=True)


signal.signal(signal.SIGINT, _on_sigint)


# ---------------------------------------------------------------- HTTP

class Client:
    """Bremst sich selbst und weicht bei 429/5xx zurueck."""

    def __init__(self, rps: float):
        self.min_gap = 1.0 / rps if rps > 0 else 0.0
        self.last = 0.0
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.calls = 0
        self.waited = 0.0

    def get(self, path: str, params: dict | None = None, tries: int = 6):
        backoff = 2.0
        for attempt in range(tries):
            gap = self.min_gap - (time.monotonic() - self.last)
            if gap > 0:
                time.sleep(gap)
                self.waited += gap
            self.last = time.monotonic()
            self.calls += 1
            try:
                r = self.s.get(f"{API}{path}", params=params, timeout=60)
            except requests.RequestException as e:
                if attempt == tries - 1:
                    raise
                print(f"    netzfehler {type(e).__name__}, warte {backoff:.0f}s", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue

            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("Retry-After", backoff))
                print(f"    HTTP {r.status_code}, warte {wait:.0f}s", flush=True)
                time.sleep(wait)
                backoff = min(backoff * 2, 120)
                continue
            print(f"    HTTP {r.status_code} fuer {path} - uebersprungen", flush=True)
            return None
        return None


# ---------------------------------------------------------------- DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
  game_id TEXT PRIMARY KEY,
  start_ts INTEGER, end_ts INTEGER, duration_s INTEGER,
  type TEXT, mode TEXT, ranked_type TEXT, player_teams TEXT,
  difficulty TEXT, num_players INTEGER, max_players INTEGER, lobby_fill_ms INTEGER,
  git_commit TEXT, map TEXT, map_size TEXT, bots INTEGER, num_turns INTEGER,
  winner_kind TEXT, winner_id TEXT,
  meta_at INTEGER, meta_json BLOB,
  full_at INTEGER, full_path TEXT, full_bytes INTEGER, full_intents INTEGER
);
CREATE INDEX IF NOT EXISTS ix_games_meta ON games(meta_at);
CREATE INDEX IF NOT EXISTS ix_games_full ON games(full_at);
CREATE INDEX IF NOT EXISTS ix_games_start ON games(start_ts);
CREATE INDEX IF NOT EXISTS ix_games_commit ON games(git_commit);

CREATE TABLE IF NOT EXISTS players (
  game_id TEXT, client_id TEXT, username TEXT, clan_tag TEXT,
  is_winner INTEGER, killed_at INTEGER, final_tiles INTEGER,
  kills INTEGER, betrayals INTEGER, gold_max INTEGER,
  stats_json TEXT,
  PRIMARY KEY (game_id, client_id)
);
CREATE INDEX IF NOT EXISTS ix_players_user ON players(username);
CREATE INDEX IF NOT EXISTS ix_players_clan ON players(clan_tag);

CREATE TABLE IF NOT EXISTS windows (
  start_iso TEXT, end_iso TEXT, filters TEXT, total INTEGER, done_at INTEGER,
  PRIMARY KEY (start_iso, end_iso, filters)
);
"""


def db() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(SCHEMA)
    return c


def num(v, default=None):
    """Die API liefert Zahlen haeufig als Strings; robust nach int."""
    if v is None:
        return default
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v))
        except ValueError:
            return default
    if isinstance(v, list):
        tot = 0
        for x in v:
            n = num(x)
            if n is not None:
                tot += n
        return tot
    return default


# ---------------------------------------------------------------- Stufe 1

def stage_list(cx: Client, con: sqlite3.Connection, start: str, end: str, filters: dict):
    d0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    d1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    fkey = json.dumps(filters, sort_keys=True)
    added = seen = 0

    cur = d0
    while cur < d1 and not _stop:
        win_end = min(cur + MAX_WINDOW, d1)
        s_iso = cur.strftime("%Y-%m-%dT%H:%M:%SZ")
        e_iso = win_end.strftime("%Y-%m-%dT%H:%M:%SZ")

        done = con.execute(
            "SELECT done_at FROM windows WHERE start_iso=? AND end_iso=? AND filters=?",
            (s_iso, e_iso, fkey),
        ).fetchone()
        if done and done[0]:
            print(f"  {s_iso[:10]}..{e_iso[:10]}  schon erledigt")
            cur = win_end
            continue

        offset, total = 0, None
        while not _stop:
            p = {"start": s_iso, "end": e_iso, "limit": PAGE, "offset": offset}
            p.update(filters)
            r = cx.get("/public/games", p)
            if r is None:
                break
            rows = r.json()
            if total is None:
                m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
                total = int(m.group(1)) if m else len(rows)
                print(f"  {s_iso[:10]}..{e_iso[:10]}  {total} Spiele")
            if not rows:
                break
            for g in rows:
                seen += 1
                cs = con.execute(
                    """INSERT INTO games (game_id,start_ts,end_ts,duration_s,type,mode,
                          ranked_type,player_teams,difficulty,num_players,max_players,lobby_fill_ms)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(game_id) DO NOTHING""",
                    (
                        g["game"],
                        iso_ms(g.get("start")), iso_ms(g.get("end")),
                        None, g.get("type"), g.get("mode"), g.get("rankedType"),
                        str(g.get("playerTeams")) if g.get("playerTeams") is not None else None,
                        g.get("difficulty"), g.get("numPlayers"), g.get("maxPlayers"),
                        g.get("lobbyFillTime"),
                    ),
                )
                added += cs.rowcount
            con.commit()
            offset += len(rows)
            print(f"    {offset}/{total}  (+{added} neu)", flush=True)
            if offset >= (total or 0) or len(rows) < PAGE:
                break

        if not _stop:
            con.execute(
                "INSERT OR REPLACE INTO windows VALUES (?,?,?,?,?)",
                (s_iso, e_iso, fkey, total or 0, int(time.time())),
            )
            con.commit()
        cur = win_end

    print(f"[list] {seen} gesehen, {added} neu in der Datenbank")


def iso_ms(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


# ---------------------------------------------------------------- Stufe 2

def stage_meta(cx: Client, con: sqlite3.Connection, limit: int, where: str):
    q = "SELECT game_id FROM games WHERE meta_at IS NULL"
    if where:
        q += f" AND ({where})"
    q += " ORDER BY start_ts DESC LIMIT ?"
    ids = [r[0] for r in con.execute(q, (limit,))]
    print(f"[meta] {len(ids)} Spiele offen")

    ok = miss = 0
    for i, gid in enumerate(ids, 1):
        if _stop:
            break
        r = cx.get(f"/public/game/{gid}", {"turns": "false"})
        if r is None:
            con.execute("UPDATE games SET meta_at=-1 WHERE game_id=?", (gid,))
            con.commit()
            miss += 1
            continue
        try:
            save_meta(con, gid, r.json())
            ok += 1
        except Exception as e:
            print(f"    {gid}: parse-fehler {type(e).__name__}: {e}")
            miss += 1
        if i % 50 == 0:
            con.commit()
            print(f"    {i}/{len(ids)}  ok={ok} fehlend={miss}", flush=True)
    con.commit()
    print(f"[meta] fertig: ok={ok} fehlend={miss}")


def save_meta(con, gid, doc):
    info = doc.get("info", {})
    cfg = info.get("config", {}) or {}
    winner = info.get("winner") or [None, None]
    wkind = winner[0] if isinstance(winner, list) and winner else None
    wid = winner[1] if isinstance(winner, list) and len(winner) > 1 else None

    con.execute(
        """UPDATE games SET git_commit=?, map=?, map_size=?, bots=?, num_turns=?,
             winner_kind=?, winner_id=?, duration_s=?, meta_at=?, meta_json=?
           WHERE game_id=?""",
        (
            doc.get("gitCommit"), cfg.get("gameMap"), cfg.get("gameMapSize"),
            num(cfg.get("bots")), num(info.get("num_turns")),
            wkind, wid, num(info.get("duration")),
            int(time.time()), zstd.compress(json.dumps(doc).encode(), 10),
            gid,
        ),
    )

    for p in info.get("players", []) or []:
        st = p.get("stats", {}) or {}
        kills = st.get("kills")
        con.execute(
            """INSERT INTO players VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id,client_id) DO UPDATE SET
                 username=excluded.username, clan_tag=excluded.clan_tag,
                 is_winner=excluded.is_winner, killed_at=excluded.killed_at,
                 final_tiles=excluded.final_tiles, kills=excluded.kills,
                 betrayals=excluded.betrayals, gold_max=excluded.gold_max,
                 stats_json=excluded.stats_json""",
            (
                gid, p.get("clientID"), p.get("username"), p.get("clanTag"),
                1 if p.get("clientID") == wid else 0,
                num(st.get("killedAt")), num(st.get("finalTiles")),
                len(kills) if isinstance(kills, list) else num(kills, 0),
                num(st.get("betrayals"), 0),
                max([num(x, 0) or 0 for x in (st.get("gold") or [])], default=0),
                json.dumps(st, separators=(",", ":")),
            ),
        )


# ---------------------------------------------------------------- Stufe 3

def raw_path(gid: str) -> Path:
    return RAW / gid[:2] / f"{gid}.json.zst"


def stage_full(cx: Client, con: sqlite3.Connection, limit: int, where: str):
    q = "SELECT game_id FROM games WHERE meta_at>0 AND full_at IS NULL"
    if where:
        q += f" AND ({where})"
    q += " ORDER BY start_ts DESC LIMIT ?"
    ids = [r[0] for r in con.execute(q, (limit,))]
    print(f"[full] {len(ids)} Spiele offen")

    ok = miss = skip = 0
    tot_bytes = 0
    for i, gid in enumerate(ids, 1):
        if _stop:
            break
        p = raw_path(gid)
        if p.exists():  # schon da, nur Index nachziehen
            mark_full(con, gid, p)
            skip += 1
            continue
        r = cx.get(f"/public/game/{gid}")
        if r is None:
            con.execute("UPDATE games SET full_at=-1 WHERE game_id=?", (gid,))
            con.commit()
            miss += 1
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(zstd.compress(r.content, 19))
        os.replace(tmp, p)          # atomar: nie eine halbe Datei auf Platte
        tot_bytes += p.stat().st_size
        mark_full(con, gid, p, r.content)
        ok += 1
        if i % 25 == 0:
            con.commit()
            mb = tot_bytes / 1e6
            print(f"    {i}/{len(ids)}  ok={ok} fehlend={miss} uebersprungen={skip}  {mb:.0f} MB", flush=True)
    con.commit()
    print(f"[full] fertig: ok={ok} fehlend={miss} uebersprungen={skip}  {tot_bytes/1e6:.0f} MB")


def mark_full(con, gid, path: Path, content: bytes | None = None):
    n_int = None
    if content is not None:
        try:
            d = json.loads(content)
            n_int = sum(len(t.get("intents", [])) for t in d.get("turns", []))
        except Exception:
            pass
    con.execute(
        "UPDATE games SET full_at=?, full_path=?, full_bytes=?, full_intents=COALESCE(?,full_intents) WHERE game_id=?",
        (int(time.time()), str(path.relative_to(DATA)), path.stat().st_size, n_int, gid),
    )


# ---------------------------------------------------------------- Stats

def stage_stats(con: sqlite3.Connection):
    def one(q, *a):
        return con.execute(q, a).fetchone()[0]

    print("=== Datenbank ===")
    print(f"  Spiele gelistet : {one('SELECT COUNT(*) FROM games'):,}")
    print(f"  mit Metadaten   : {one('SELECT COUNT(*) FROM games WHERE meta_at>0'):,}")
    print(f"  mit vollem Rec. : {one('SELECT COUNT(*) FROM games WHERE full_at>0'):,}")
    print(f"  Spieler-Zeilen  : {one('SELECT COUNT(*) FROM players'):,}")
    print(f"  distinkte User  : {one('SELECT COUNT(DISTINCT username) FROM players'):,}")
    b = one("SELECT COALESCE(SUM(full_bytes),0) FROM games")
    it = one("SELECT COALESCE(SUM(full_intents),0) FROM games")
    print(f"  Records auf HDD : {b/1e6:.1f} MB")
    print(f"  Intents gesamt  : {it:,}")
    for label, q in (
        ("Modi", "SELECT mode, COUNT(*) FROM games GROUP BY mode ORDER BY 2 DESC LIMIT 6"),
        ("Ranked", "SELECT ranked_type, COUNT(*) FROM games GROUP BY ranked_type ORDER BY 2 DESC LIMIT 6"),
        ("Commits", "SELECT git_commit, COUNT(*) FROM games WHERE git_commit IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 6"),
        ("Maps", "SELECT map, COUNT(*) FROM games WHERE map IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 6"),
    ):
        rows = con.execute(q).fetchall()
        if rows:
            print(f"  {label}: " + ", ".join(f"{k or '-'}={v:,}" for k, v in rows))


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rps", type=float, default=1.0, help="Anfragen pro Sekunde (Standard 1.0)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("list");  a.add_argument("--start", required=True); a.add_argument("--end", required=True)
    a.add_argument("--type"); a.add_argument("--mode"); a.add_argument("--ranked-type")

    b = sub.add_parser("meta");  b.add_argument("--limit", type=int, default=1000); b.add_argument("--where", default="")
    c = sub.add_parser("full");  c.add_argument("--limit", type=int, default=100);  c.add_argument("--where", default="")
    sub.add_parser("stats")

    args = ap.parse_args()
    con = db()
    cx = Client(args.rps)
    t0 = time.time()

    if args.cmd == "list":
        f = {}
        if args.type: f["type"] = args.type
        if args.mode: f["mode"] = args.mode
        if args.ranked_type: f["rankedType"] = args.ranked_type
        stage_list(cx, con, args.start, args.end, f)
    elif args.cmd == "meta":
        stage_meta(cx, con, args.limit, args.where)
    elif args.cmd == "full":
        stage_full(cx, con, args.limit, args.where)
    elif args.cmd == "stats":
        stage_stats(con)
        return

    dt = time.time() - t0
    print(f"[http] {cx.calls} Anfragen in {dt:.0f}s ({cx.calls/max(dt,1):.2f}/s, {cx.waited:.0f}s gedrosselt)")
    con.close()


if __name__ == "__main__":
    main()
