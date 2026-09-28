#!/usr/bin/env bash
# node-1: paket.sh <host> <phase> — tar-Strom der Records dieses Hosts und dieser Phase auf stdout,
# Pfade <phase>/xx/<gid>.json. p1 = v0.33.11-14 (88cc95d8, 8b45be57, 577819ba, 0cb90ccb), p2 = Rest.
set -eu
cd ~/ur-w
H=$1; P=$2
tmp=$(mktemp -d)
mkdir -p "$tmp/$P"
awk -F'\t' -v h="$H" -v p="$P" 'NR > 1 && $4 == h {
  k = ($2 == "88cc95d8" || $2 == "8b45be57" || $2 == "577819ba" || $2 == "0cb90ccb") ? "p1" : "p2"
  if (k == p) print $1 }' verteilung.tsv > "$tmp/ids"
while read -r g; do
  mkdir -p "$tmp/$P/${g:0:2}"
  ln -s "$HOME/ur-w/rec/${g:0:2}/$g.json" "$tmp/$P/${g:0:2}/$g.json"
done < "$tmp/ids"
tar chf - -C "$tmp" "$P"
rm -rf "$tmp"
