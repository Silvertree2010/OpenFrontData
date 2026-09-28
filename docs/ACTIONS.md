# Aktionsraum — Vertrag zwischen Record, Netz und Engine

`env/actions.py`. Getestet: 286.298 echte Züge, 100 % Round-Trip auf allen
nicht-räumlichen Feldern (`env/test_actions.py`).

## Köpfe (Ausgänge des Netzes)

| Kopf | Größe | wofür |
|---|---|---|
| atype | 21 | Aktionstyp (20 Züge + no_op) |
| target | 26 | Zeiger in Gegner-Topliste (24) + NEUTRAL + ALLE |
| coarse | 16200 | grobe Kachel, 180×90 |
| fine | 64 | feine Kachel, 8×8 im Grobfeld |
| unit_type | 10 | City…MIRV |
| own_ref | 128 | Zeiger in eigene Einheiten/Angriffe |
| magnitude | 10 | Bruchteil-Klasse (Truppen/Gold) |
| emoji | 64 · quickchat | 35 · embargo_start 2 · rocket_up 2 · amount 51 |

Pro Aktionstyp ist nur eine Teilmenge aktiv (`HEAD_SCHEMA`) — das ist die
BC-Verlustmaske: supervidiert werden nur die Köpfe, die der Typ verwendet.

## Der Kontext-Handshake (das noch Offene)

Vier Felder brauchen den Spielzustand im Entscheidungstick, gebündelt in
`actions.Context`:

- `magnitude` → braucht `troops`/`gold` (Nenner)
- `target`    → braucht `opp_ids` in **derselben Reihenfolge wie obs.encodeVec**
- `own_ref`   → braucht `own_unit_ids` / `own_attack_ids`
- `tile`      → braucht `map_w`/`map_h`

Beim **Training** füllt obs.ts diesen Kontext während des Replays (gleiche
Gegner-Sortierung, gleiche Maße). Beim **Spielen** kommt er aus der Live-Engine.
Das ist der nächste Schritt und braucht `vendor/` → wartet auf den Arch-PC.

## Bewusste v1-Grenzen

- `move_warship` mit mehreren Schiffen (154/1410) — Einzelzeiger, nicht darstellbar.
- `quick_chat` optionaler zweiter Zeiger (`target`) — ausgelassen.
- Kachel-Präzision ~1 Kachel (Raster feiner als Karte auf kleinen Karten =
  Über-Auflausung; Engine rastet beim Spielen ohnehin auf legale Kachel).
