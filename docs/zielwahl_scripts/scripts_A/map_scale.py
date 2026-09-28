"""Physical size of one coarse cell / one 1440x720 grid unit per (map,size), weighted by the
index.sqlite game population (proxy for the training pool). Read-only."""
import json, glob, os, sqlite3, collections
import numpy as np
MAN={}
for m in glob.glob("/tmp/openfront-ai/arbeit/amssp/OpenFrontIO/resources/maps/*/manifest.json"):
    j=json.load(open(m)); MAN[j["name"].lower()]=j
c=sqlite3.connect("file:~/openfront-ai/data/index.sqlite?mode=ro",uri=True)
print("ranked_type", c.execute("select ranked_type,count(*) from games group by 1 order by 2 desc limit 6").fetchall())
print("type", c.execute("select type,count(*) from games group by 1 order by 2 desc limit 6").fetchall())
rows=c.execute("select map,map_size,count(*) from games where type='Public' group by 1,2").fetchall()
cw=[];ch=[];gx=[];gy=[];an=[];wts=[];miss=collections.Counter()
for mp,sz,n in rows:
    j=MAN.get((mp or "").lower())
    if not j: miss[mp]+=n; continue
    d=j["map"] if sz=="Normal" else j["map4x"]
    W,H=d["width"],d["height"]
    cw.append(W/180); ch.append(H/90); gx.append(W/1440); gy.append(H/720); an.append((W/180)/(H/90)); wts.append(n)
w=np.array(wts,float); w/=w.sum()
def q(a,name):
    a=np.array(a); o=np.argsort(a); cs=np.cumsum(w[o])
    p=lambda t: a[o][np.searchsorted(cs,t)]
    print(f"{name:34s} p5 {p(.05):6.2f}  p25 {p(.25):6.2f}  median {p(.5):6.2f}  p75 {p(.75):6.2f}  p95 {p(.95):6.2f}")
print("games matched", int(sum(wts)), "unmatched", sum(miss.values()), miss.most_common(5))
print("size split", c.execute("select map_size,count(*) from games where type='Public' group by 1").fetchall())
q(cw,"coarse cell width [tiles]"); q(ch,"coarse cell height [tiles]")
q(gx,"grid unit x [tiles] (W/1440)"); q(gy,"grid unit y [tiles] (H/720)")
q(an,"cell aspect w/h (1=square)")
q([5*a for a in cw],"5x5-cell receptive field width [tiles]")
fr_w=sum(wi for wi,a in zip(w,gx) if a<1); fr_h=sum(wi for wi,a in zip(w,gy) if a<1)
print(f"share of games with W<1440 (fine cols over-resolved): {fr_w:.1%}; with H<720: {fr_h:.1%}")
