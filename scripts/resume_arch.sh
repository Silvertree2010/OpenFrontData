#!/bin/sh
# Auf Arch: ~1150 offene Records von den Helfern zurueckholen und Arch wieder mitrechnen
# lassen. Helfer werden auf ihren Rest reduziert + neu gestartet, dann Arch gestartet.
P=/home/netter/projects/openfront-ai
cd "$P" || exit 1
AP=apollo
N2=netter@100.81.22.70

# pull-Skripte auf Helfer
for H in "$AP" "$N2"; do
  scp -q /tmp/helper_pull.py "$H:/tmp/helper_pull.py"
  scp -q /tmp/stop_pull.sh   "$H:/tmp/stop_pull.sh"
  scp -q /tmp/restart.sh     "$H:/tmp/restart.sh"
done

# apollo: 615 zurueck
ssh -o BatchMode=yes "$AP" "sh /tmp/stop_pull.sh 615"
rsync -a "$AP:/tmp/pull/" "$P/data/night_raw/"
ssh -o BatchMode=yes "$AP" "rm -rf /tmp/pull; sh /tmp/restart.sh records shards 6 1 /home/netter/of-work mat.log"

# node2: 537 zurueck
ssh -o BatchMode=yes "$N2" "sh /tmp/stop_pull.sh 537"
rsync -a "$N2:/tmp/pull/" "$P/data/night_raw/"
ssh -o BatchMode=yes "$N2" "rm -rf /tmp/pull; sh /tmp/restart.sh records shards 4 0 /home/netter/of-work mat.log"

# Arch wieder starten (8 Container, volle CPU)
sh /tmp/restart.sh data/night_raw data/shards 8 0 "$P" logs/mat1.log

echo "night_raw offen jetzt: $($P/.venv/bin/python -c "import glob,os;n=glob.glob('$P/data/night_raw/**/*.json',recursive=True);print(sum(1 for f in n if not os.path.exists(os.path.join('$P/data/shards',os.path.basename(f)[:-5]+'.meta.zst'))))")"
