#!/usr/bin/env bash
# Kompletter Tier-1-Nachweis auf arch (nice 10, höchstens 3 Prozesse).
#   bash nachweis.sh   → Protokoll auf stdout, Ergebnisse unter ~/mat-dev/t1out
set -u
T=~/mat-dev/t1
O=~/mat-dev/t1out
S=~/mat-dev/w/88cc95d8/mat-tier1/tests/tier1
PY=~/mat-dev/w/88cc95d8/mat-tier1/py/tier1.py
TIMEFORMAT="  py %Rs"
rm -rf $O/88cc95d8 $O/8b45be57 $O/115da032 $O/brk

echo "== full (Referenz)"
bash $S/run_arch.sh $T/list.tsv $O full

echo "== verify"
while IFS=$'\t' read -r c g p; do
  D=$O/$c/$g
  time timeout 900 nice -n 10 python3 $PY verify $D $g --ref $D/$g.t1ref.json
  echo "  exit=$?"
done < $T/list.tsv

echo "== Brüche, Schnitt, Korruption (dJtnLxJA, 88cc95d8)"
R=$(awk -F'\t' '$2=="dJtnLxJA"{print $3}' $T/list.tsv)
B=$O/brk
cd ~/mat-dev/w/88cc95d8
for t in "state@chk" "unit@1000"; do
  d=$B/${t%%@*}
  timeout 600 nice -n 10 npx tsx mat-tier1/tests/tier1/harness.ts $R $d --mode full --break $t > /dev/null 2>&1
  python3 -c "import json,glob; print('BRUCH:', json.load(open(glob.glob('$d/*.t1run.*.json')[0]))['brkInfo'])"
  timeout 300 python3 $PY verify $d dJtnLxJA --ref $d/dJtnLxJA.t1ref.json | head -4
  echo "  exit=${PIPESTATUS[0]} (erwartet 1)"
done
d=$B/vu
timeout 600 nice -n 10 npx tsx mat-tier1/tests/tier1/harness.ts $R $d --mode full --valid-until 3000 > /dev/null 2>&1
python3 -c "import json,glob; r=json.load(open(glob.glob('$d/*.t1run.*.json')[0])); print('VALID_UNTIL 3000: chkDropped', r['tier1']['chkDropped'], 'gewählt', r['chkTicks'])"
timeout 300 python3 $PY verify $d dJtnLxJA --ref $d/dJtnLxJA.t1ref.json
echo "  exit=$? (erwartet 0)"
python3 - <<EOF
import sys; sys.path.insert(0, "$(dirname $PY)"); import tier1
o = tier1.Own("$d/dJtnLxJA.own.zst"); u = tier1.Units("$d/dJtnLxJA.units.zst")
print("  max_tick", o.max_tick, "last_tick", o.last_tick, "units max tick", max(r[0] for r in u.rows()))
try:
    o.state(3001); print("  FEHLER: state(3001) geliefert")
except tier1.NotValid as e:
    print("  state(3001) verweigert:", e)
EOF
d=$B/corrupt
mkdir -p $d
cp $O/88cc95d8/dJtnLxJA/dJtnLxJA.* $d/
python3 -c "p='$d/dJtnLxJA.own.zst'; b=bytearray(open(p,'rb').read()); i=len(b)//2; b[i]^=0x40; open(p,'wb').write(b); print('Byte', i, 'von', len(b), 'gekippt')"
timeout 300 python3 $PY verify $d dJtnLxJA --ref $d/dJtnLxJA.t1ref.json | head -3
echo "  exit=${PIPESTATUS[0]} (erwartet 1)"

echo "== Zeit/RSS: replay, dann tier1"
bash $S/run_arch.sh $T/list.tsv $O replay
bash $S/run_arch.sh $T/list.tsv $O tier1

echo "== Kodierungs-Experiment (neues Format als Basis)"
cd $O
timeout 2400 nice -n 10 python3 $S/enc_experiment.py 8b45be57/H2NkRg5R H2NkRg5R 115da032/A9iejjLi A9iejjLi 88cc95d8/AgmJxf2p AgmJxf2p 88cc95d8/FCxzAh2Y FCxzAh2Y

echo "== Auswertung"
timeout 600 python3 $S/summary.py
echo "== ENDE"
