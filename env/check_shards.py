"""
Shard-Pruefung: stimmen Metazeilen und Kartenblöcke ueberein?

Eine Metazeile ohne zugehoerigen Kartenblock verschiebt alle folgenden Samples
derselben Partie auf die falsche Karte — stille Beschaedigung, die das Training
nicht bemerkt, weil jede einzelne Karte fuer sich gueltig aussieht. Deshalb wird
hier beim EINSAMMELN geprueft, nicht erst beim Lernen.

Die Karten werden nicht entpackt: es reicht, den Laengen-Praefixen zu folgen.

  python3 env/check_shards.py <shard_dir> [--loeschen]     # Rueckgabe != 0 bei Fund
"""
from __future__ import annotations
import os, sys, glob, struct
from compression import zstd

MAPLEN_MIN = 16          # ein zstd-Block ist nie kleiner
MAPLEN_MAX = 4_000_000   # und nie groesser als eine ganze Karte roh


def bloecke(maps_path):
    """Anzahl vollstaendiger Blöcke; wirft bei abgeschnittenem/kaputtem Strom."""
    n = 0
    size = os.path.getsize(maps_path)
    with open(maps_path, "rb") as f:
        while True:
            head = f.read(4)
            if not head:
                return n
            if len(head) < 4:
                raise ValueError(f"abgeschnittener Laengen-Praefix bei Block {n}")
            ln = struct.unpack("<I", head)[0]
            if not (MAPLEN_MIN <= ln <= MAPLEN_MAX):
                raise ValueError(f"unplausible Blocklaenge {ln} bei Block {n}")
            if f.tell() + ln > size:
                raise ValueError(f"Block {n} reicht ueber das Dateiende hinaus")
            f.seek(ln, 1)
            n += 1


def pruefe(meta_path):
    gid = os.path.basename(meta_path)[:-9]
    maps_path = meta_path[:-9] + ".maps"
    zeilen = sum(1 for l in zstd.decompress(open(meta_path, "rb").read()).decode().split("\n") if l)
    if zeilen == 0:
        return gid, "leer", 0, 0        # nur bei Altbestand; neu gibt es dafuer .none
    if not os.path.exists(maps_path):
        return gid, "maps fehlt", zeilen, -1
    try:
        n = bloecke(maps_path)
    except ValueError as e:
        return gid, str(e), zeilen, -1
    if n != zeilen:
        return gid, f"{zeilen} Metazeilen, {n} Kartenblöcke", zeilen, n
    return None


def main():
    d = sys.argv[1]
    loeschen = "--loeschen" in sys.argv
    metas = sorted(glob.glob(os.path.join(d, "*.meta.zst")))
    schlecht = []
    for m in metas:
        r = pruefe(m)
        if r:
            schlecht.append(r)
            print(f"KAPUTT {r[0]}: {r[1]}")
            if loeschen:
                for p in (m, m[:-9] + ".maps"):
                    try: os.unlink(p)
                    except FileNotFoundError: pass
    print(f"{len(metas)} Shards geprueft, {len(schlecht)} beanstandet"
          + (" (geloescht)" if loeschen and schlecht else ""))
    return 1 if schlecht else 0


if __name__ == "__main__":
    sys.exit(main())
