#!/usr/bin/env bash
# materializer/fleet/launch.sh — Materialisierer v2 auf mehreren Hosts starten.
#
# Verteilt SHARD=i/n nach Zeilenposition der Hostliste (materializer/fleet/
# hosts.example-Format), baut das Image auf dem Host ("build") oder verteilt
# es per `docker save | ssh ... docker load` ("load") und startet
# `docker run -d --name of-mat2 --cpus N --user uid:gid ...` (DESIGN.md §7).
#
# Jeder ssh-Aufruf steckt in `timeout`, wie vom Nutzer verlangt (parallele
# Sessions/haengende Verbindungen sollen das Skript nicht aufhalten).
#
# Aufruf:
#   materializer/fleet/launch.sh <hosts-datei> [-e KEY=VAL ...]
#
# Beispiel:
#   materializer/fleet/launch.sh materializer/fleet/hosts.local -e NOOP_EVERY=200 -e TIER1_PCT=50
set -euo pipefail

HOSTS_FILE="${1:?Aufruf: materializer/fleet/launch.sh <hosts-datei> [-e KEY=VAL ...]}"
shift
EXTRA_ENV=("$@")   # z.B. -e NOOP_EVERY=200 -e RETRY_ERR=1, wird an docker run durchgereicht

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAT_VERSION="$(cd "$REPO_DIR" && git rev-parse HEAD 2>/dev/null || echo unknown)"
IMAGE="of-mat2:${MAT_VERSION}"
DOCKERFILE="materializer/docker/Dockerfile"

mapfile -t LINES < <(grep -vE '^[[:space:]]*(#|$)' "$HOSTS_FILE")
N="${#LINES[@]}"
if [ "$N" -eq 0 ]; then echo "[launch] keine aktiven Hosts in $HOSTS_FILE" >&2; exit 1; fi

echo "[launch] $N Host(s), MAT_VERSION=$MAT_VERSION, Image=$IMAGE"

LOCAL_BUILD_DONE=0
ensure_local_image() {
  if [ "$LOCAL_BUILD_DONE" -eq 0 ]; then
    echo "[launch] baue lokal fuer load-Verteilung: $IMAGE"
    docker build -f "$REPO_DIR/$DOCKERFILE" --build-arg "MAT_VERSION=$MAT_VERSION" -t "$IMAGE" "$REPO_DIR"
    LOCAL_BUILD_DONE=1
  fi
}

i=0
for line in "${LINES[@]}"; do
  read -r NAME SSH RECORDS OUTDIR CPUS MODE <<< "$line"
  echo "[launch] ($((i+1))/$N) $NAME ($SSH): SHARD=${i}/${N} cpus=${CPUS} modus=${MODE}"

  timeout 15 ssh -o ConnectTimeout=10 "$SSH" "mkdir -p '$RECORDS' '$OUTDIR'"

  case "$MODE" in
    build)
      timeout 900 ssh -o ConnectTimeout=10 "$SSH" \
        "nice -n 10 docker build -f '$REPO_DIR/$DOCKERFILE' --build-arg MAT_VERSION='$MAT_VERSION' -t '$IMAGE' '$REPO_DIR'"
      ;;
    load)
      ensure_local_image
      echo "[launch]   docker save | ssh $SSH docker load"
      docker save "$IMAGE" | timeout 600 ssh -o ConnectTimeout=10 "$SSH" "docker load"
      ;;
    *)
      echo "[launch] unbekannter build-Modus '$MODE' fuer $NAME (build|load erwartet)" >&2
      exit 1
      ;;
  esac

  # --user als $(id -u):$(id -g) MUSS auf dem Zielhost laufen, deshalb escaped
  # ($\(id -u\) statt $(id -u)) -- sonst wuerde die lokale Shell es hier ausführen.
  timeout 30 ssh -o ConnectTimeout=10 "$SSH" \
    "docker rm -f of-mat2 >/dev/null 2>&1; docker run -d --name of-mat2 --restart=no \
     --cpus '$CPUS' --user \$(id -u):\$(id -g) \
     -e SHARD='${i}/${N}' -e MAT_VERSION='$MAT_VERSION' ${EXTRA_ENV[*]:-} \
     -v '$RECORDS':/in:ro -v '$OUTDIR':/out '$IMAGE'"

  i=$((i+1))
done

echo "[launch] fertig. Status pruefen: materializer/fleet/status.sh $HOSTS_FILE"
