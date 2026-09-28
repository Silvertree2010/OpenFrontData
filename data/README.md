# Daten

- `reputation.json` — Spielername zu Reputationswert. Das Netz bekommt diesen Wert als Eingabe;
  ohne die Datei kann kein Inferenzserver starten. Erzeugt aus öffentlichen Partien (`scraper/rate.py`).
- `ultimus_rex/` — Skripte, mit denen aus Video-Transkripten eines starken Spielers Strategiewissen
  destilliert wurde. Die Transkripte selbst liegen nicht hier.

Leaderboard-Auszug und Elo-Datenbank sind nicht enthalten; beides erzeugt `scraper/` neu.
