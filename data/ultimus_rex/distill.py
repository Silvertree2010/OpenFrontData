import json, re, collections
ADVICE = ["you want","you should","you need","you have to","always","never","the key",
"the trick","make sure","priority","important","the reason","if you","when you","don't",
"avoid","i always","i never","the best","better to","tend to","watch for","watch out",
"focus on","first thing","early game","late game","mid game","rule of thumb","big mistake",
"the goal","ideally","key thing","what you do","the idea","this is why","that's why"]
TERM = ["nuke","atom bomb","hydrogen","mirv","sam","silo","city","cities","port","factory",
"defense post","troop","gold","alliance","ally","allies","betray","boat","warship","trade",
"spawn","expand","border","attack","target","chokepoint","wall","population","ratio","tile",
"annex","front","retreat","siege","stall","income","economy","defend","offense"]
adv=re.compile("|".join(re.escape(a) for a in ADVICE), re.I)
term=re.compile("|".join(re.escape(t) for t in TERM), re.I)

seen=set(); scored=[]
for line in open("data/ultimus_rex/transcripts.jsonl"):
    d=json.loads(line); txt=d["text"]
    # in Sätze zerlegen
    for s in re.split(r"(?<=[.!?])\s+", txt):
        s=s.strip()
        w=len(s.split())
        if w<6 or w>45: continue
        na=len(set(m.group(0).lower() for m in adv.finditer(s)))
        nt=len(set(m.group(0).lower() for m in term.finditer(s)))
        if na==0 or nt==0: continue
        score=na*2+nt
        key=re.sub(r"[^a-z ]","",s.lower())[:80]
        if key in seen: continue
        seen.add(key)
        scored.append((score,s))
scored.sort(reverse=True)
top=scored[:450]
with open("data/ultimus_rex/highsignal.txt","w") as f:
    for sc,s in top: f.write(f"[{sc}] {s}\n")
print(f"Sätze gesamt gescored: {len(scored):,}")
print(f"Top 450 → highsignal.txt ({sum(len(s.split()) for _,s in top):,} Wörter)")
print("--- Beispiele (höchster Score) ---")
for sc,s in top[:8]: print(f"  [{sc}] {s[:140]}")
