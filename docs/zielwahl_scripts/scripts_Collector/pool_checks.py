"""Collector spot-checks on the 65 local records + index.sqlite (read-only).
(1) spawn intents before/after the real spawn-phase end (300 turns, 150 with randomSpawn)
(2) humans per game with a spawn intent (-> size of 'last spawn per human' sample set)
(3) upper bound of NOOP_EVERY=200 samples per game vs action samples -> spatial share with no-ops
(4) pool size / types in index.sqlite"""
import glob, json, collections, sqlite3
from compression import zstd
import numpy as np
P="~/openfront-ai/"
SPATIAL={"build_unit","boat","move_warship"}
pre=post=0; humans_spawn=[]; noop_ub=[]; act=[]; spat=[]; last_spawn_per_h=[]
for p in sorted(glob.glob(P+"data/raw/*/*.json.zst")):
    j=json.loads(zstd.decompress(open(p,"rb").read()))
    rs=bool(j["info"]["config"].get("randomSpawn")); lim=150 if rs else 300
    first=collections.defaultdict(lambda:None); last=collections.defaultdict(int)
    lastatk={}; a=s=0; hs=set(); nspawn_h=collections.Counter()
    for t in j["turns"]:
        tn=t.get("turnNumber",0)
        for i in t.get("intents") or []:
            ty=i.get("type"); cid=i.get("clientID")
            if not cid or ty=="mark_disconnected": continue
            if ty=="spawn":
                if tn<lim: pre+=1; hs.add(cid); nspawn_h[cid]+=1
                else: post+=1
                continue
            if tn<lim: continue
            if first[cid] is None: first[cid]=tn
            last[cid]=tn
            if ty=="attack":
                la=lastatk.get(cid)
                if la is not None and tn-la<30: continue
                lastatk[cid]=tn
            a+=1; s+= ty in SPATIAL
    humans_spawn.append(len(hs)); last_spawn_per_h+=list(nspawn_h.values())
    # no-op upper bound: every client alive from spawn end until its last intent, 1/200 per tick
    noop_ub.append(sum(max(0,last[c]-lim) for c in last)/200.0)
    act.append(a); spat.append(s)
act=np.array(act); spat=np.array(spat); noop=np.array(noop_ub)
print(f"(1) spawn intents before phase end: {pre}, after: {post} ({post/(pre+post):.1%})")
print(f"(2) humans with >=1 in-phase spawn intent per game: median {np.median(humans_spawn):.0f}, mean {np.mean(humans_spawn):.1f}; spawn clicks per such human median {np.median(last_spawn_per_h):.0f}, mean {np.mean(last_spawn_per_h):.1f}")
print(f"(3) per game: action samples (THIN=30) median {np.median(act):.0f}; spatial median {np.median(spat):.0f}; no-op UB median {np.median(noop):.0f}")
print(f"    spatial share without no-ops {spat.sum()/act.sum():.1%}; with no-op upper bound {spat.sum()/(act.sum()+noop.sum()):.1%}")
c=sqlite3.connect(f"file:{P}data/index.sqlite?mode=ro",uri=True)
print("(4) games by type:", c.execute("select type,count(*) from games group by 1 order by 2 desc").fetchall())
print("    tables:", [r[0] for r in c.execute("select name from sqlite_master where type='table'")])
