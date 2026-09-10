#!/bin/sh
P=/home/netter/projects/openfront-ai
while true; do
  docker rm -f of-watch >/dev/null 2>&1
  docker run --rm --name of-watch --network host --entrypoint sh \
    -v $P/env:/app/env:ro -v $P/tsconfig.json:/app/tsconfig.json:ro -v $P/live:/out \
    -e OUT=/out/game_state.json -e INF=http://127.0.0.1:8650/act -e MAP=Europe -e SIZE=Medium -e BOTS=40 \
    of-mat -c "cd /app/vendor/openfront && npx tsx /app/env/play_watch.ts" >> $P/logs/watch.log 2>&1
  sleep 5
done
