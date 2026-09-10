#!/bin/sh
# Vor dem Shutdown: Arch-Materialisierung stoppen, Rest auf Helfer verschieben (laufen
# ueber Nacht weiter), Bildschirme an. Luefter NICHT anfassen (bleiben auto/normal).
# Skriptdatei -> Kommandozeile ohne "mat_launch" -> kein Selbst-Kill.
P=/home/netter/projects/openfront-ai
cd "$P" || exit 1

# 1) Arch stoppen
for p in $(pgrep -f mat_launch.py); do kill "$p" 2>/dev/null; done
sleep 1
IDS=$(docker ps -q --filter ancestor=of-mat); [ -n "$IDS" ] && docker kill $IDS >/dev/null 2>&1
sleep 2

# 2) Arch-Rest auf Helfer verschieben (60% apollo, 40% node2) + Helfer neu starten
"$P/.venv/bin/python" /tmp/compute_split.py
rsync -a /tmp/to_apollo/ apollo:of-work/records/ 2>/dev/null
rsync -a /tmp/to_node2/  netter@100.81.22.70:of-work/records/ 2>/dev/null
scp -q /tmp/restart.sh apollo:/tmp/restart.sh 2>/dev/null
scp -q /tmp/restart.sh netter@100.81.22.70:/tmp/restart.sh 2>/dev/null
ssh -o BatchMode=yes apollo              "sh /tmp/restart.sh records shards 6 1 /home/netter/of-work mat.log" >/dev/null 2>&1
ssh -o BatchMode=yes netter@100.81.22.70 "sh /tmp/restart.sh records shards 4 0 /home/netter/of-work mat.log" >/dev/null 2>&1
rm -rf /tmp/to_apollo /tmp/to_node2

# 3) Bildschirme an
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export HYPRLAND_INSTANCE_SIGNATURE=$(ls "$XDG_RUNTIME_DIR"/hypr 2>/dev/null | head -1)
hyprctl dispatch dpms on >/dev/null 2>&1

# Report zum Verifizieren (VOR dem Shutdown)
echo "arch_container=$(docker ps -q --filter ancestor=of-mat | wc -l) (soll 0)"
echo "monitore_an=$(hyprctl monitors 2>/dev/null | grep -c 'dpmsStatus: 1') (soll 2)"
echo "apollo rec/done=$(ssh -o BatchMode=yes apollo 'find of-work/records -name *.json|wc -l; ls of-work/shards/*.meta.zst 2>/dev/null|wc -l' | tr '\n' '/') container=$(ssh -o BatchMode=yes apollo docker ps -q --filter ancestor=of-mat|wc -l)"
echo "node2 rec/done=$(ssh -o BatchMode=yes netter@100.81.22.70 'find of-work/records -name *.json|wc -l; ls of-work/shards/*.meta.zst 2>/dev/null|wc -l' | tr '\n' '/') container=$(ssh -o BatchMode=yes netter@100.81.22.70 docker ps -q --filter ancestor=of-mat|wc -l)"
echo "zentral_shards=$(ls $P/data/shards/*.meta.zst 2>/dev/null | wc -l)"
