#!/usr/bin/env python3
"""YouTube Auto-VTT -> sauberer Text.

Auto-Captions rollen zeilenweise durch: derselbe Satz taucht 2-4x auf,
einmal als Teilzeile, einmal komplett. Das hier wirft die Duplikate raus
und liefert pro Video einen zusammenhaengenden Text.
"""
import json, re, sys
from collections import deque
from pathlib import Path

TAG = re.compile(r"<[^>]+>")          # <c>, </c>, <00:00:12.345>
CUE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s+-->")
NOISE = re.compile(r"^\[(music|applause|laughter|.*?)\]$", re.I)


def parse_vtt(path):
    lines, recent = [], deque(maxlen=6)
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if CUE.match(s):
            continue
        s = TAG.sub("", s).replace("&nbsp;", " ").strip()
        s = re.sub(r"\s+", " ", s)
        if not s or NOISE.match(s):
            continue
        if s in recent:                # rollende Wiederholung
            continue
        recent.append(s)
        lines.append(s)
    return " ".join(lines)


def main(raw_dir, meta_path, out_path):
    meta = json.loads(Path(meta_path).read_text()) if Path(meta_path).exists() else {}
    files = {}
    for p in Path(raw_dir).glob("*.vtt"):
        vid = p.name.split(".")[0]
        lang = p.name.split(".")[-2]
        # en-orig (Originalsprache) schlaegt auto-uebersetztes en
        rank = 0 if lang.startswith("en-orig") else 1
        if vid not in files or rank < files[vid][0]:
            files[vid] = (rank, p)

    kept, dropped = [], []
    for vid, (_, p) in sorted(files.items()):
        text = parse_vtt(p)
        words = len(text.split())
        if words < 200:                # leer / kaputt / kein echtes Video
            dropped.append((vid, words))
            continue
        m = meta.get(vid, {})
        kept.append({"id": vid, "title": m.get("title", ""),
                     "duration": m.get("dur"), "words": words, "text": text})

    with open(out_path, "w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    tw = sum(r["words"] for r in kept)
    print(f"VTT-Dateien : {len(files)}")
    print(f"uebernommen : {len(kept)}")
    print(f"verworfen   : {len(dropped)} (unter 200 Woerter)")
    print(f"Woerter ges.: {tw:,}  (~{tw*4//3:,} Tokens)")
    print(f"-> {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
