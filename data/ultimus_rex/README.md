# Ultimus_Rex — Strategiekorpus

`transcripts.jsonl`, 200 Videos des Kanals @ultimus_rex40, 2,41 Mio Wörter.
Ein Datensatz je Video: id, title, duration, words, text.

**Warum gesichert:** nicht aus den Partie-Aufzeichnungen herstellbar, und ein
erneuter Abzug liefert nicht dasselbe — YouTube erzeugt automatische Untertitel
rollend neu. Der Abzug dauerte rund zwei Stunden.

**Grenzen:** keine Zeitmarken (die Roh-VTTs wurden bewusst gelöscht). Die
automatischen Untertitel verhauen Spielernamen und Spielbegriffe zuverlässig.
Für Stichwortsuche nach Strategien reicht es, für Namenszuordnung nicht.

Daraus destilliert: 5635 Ratschläge in `docs/STRATEGY_SPEC.md`, erzeugt mit
`distill.py`. Regenerator für den Korpus: `clean_subs.py`.

Vorbehalt aus der Auswertung: die Videos stammen teils aus älteren Spielversionen.
Die Muster sind robust, konkrete Zahlen gehören gegen `docs/MECHANIK.md` geprüft.

Die restlichen 1210 Videos des Kanals wären etwa 15 Mio Wörter und zwei Stunden Lauf.
