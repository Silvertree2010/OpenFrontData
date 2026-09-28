"""(1) Spawn intents after the spawn phase (only these survive materialize.ts:197 '!inSpawnPhase()').
(2) Territory-size proxy: final_tiles of players in Public games (index.sqlite, read-only).
(3) Spatial share of samples from docs/INTENTS.md counts (before attack thinning)."""
import glob, json, collections, sqlite3
from compression import zstd
import numpy as np
P="~/openfront-ai/"
res=collections.defaultdict(list)
for p in sorted(glob.glob(P+"data/raw/*/*.json.zst")):
    j=json.loads(zstd.decompress(open(p,"rb").read())); rs=bool(j["info"]["config"].get("randomSpawn"))
    for t in j["turns"]:
        for i in t.get("intents") or []:
            if i.get("type")=="spawn": res[rs].append(t.get("turnNumber",0))
for rs,v in res.items():
    v=np.array(v); lim=150 if rs else 300
    print(f"(1) randomSpawn={rs}: spawn intents {len(v)}, turn>={lim}: {(v>=lim).sum()} ({(v>=lim).mean():.1%}), max {v.max()}")
c=sqlite3.connect(f"file:{P}data/index.sqlite?mode=ro",uri=True)
for sz in ("Normal","Compact"):
    v=np.array([r[0] for r in c.execute("select p.final_tiles from players p join games g using(game_id) where g.type='Public' and g.map_size=? and p.final_tiles>0",(sz,))])
    print(f"(2) {sz}: players {len(v)}  final_tiles p25/50/75/90/99 {np.percentile(v,[25,50,75,90,99]).astype(int)}")
cnt=dict(attack=181662,build=41851,boat=23721,spawn=8194,mark_disc=7189,warship=1410,total=293641)
sp=cnt["build"]+cnt["boat"]+cnt["warship"]; den=cnt["total"]-cnt["spawn"]-cnt["mark_disc"]
print(f"(3) spatial (build+boat+warship) {sp} / non-spawn non-admin {den} = {sp/den:.1%} before attack thinning; ln(16200)={np.log(16200):.2f}")
