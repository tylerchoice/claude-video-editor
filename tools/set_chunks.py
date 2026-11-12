"""Lay out caption chunks by hand: each chunk's last word gets an invisible period (end
punctuation is stripped on screen), so the caption splitter keeps exactly these chunks.
usage: python tools/set_chunks.py <name> <chunks.txt>
  chunks.txt: one chunk per line, covering every word in words_cut.json (hidden opening chunks
  included). Delete out/<name>/words_cut.prechunk.json after any recut, or the old words come back."""
import json, os, re, shutil, sys
plan = os.path.abspath(sys.argv[2])
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel

name = sys.argv[1]
d = reel.workdir(name)
p, pre, lp = d / "words_cut.json", d / "words_cut.prechunk.json", d / "layout.json"
sc = reel.STYLE.get("small_captions", {})
max_chars, four = sc.get("max_chars", 18), sc.get("four_word_chars", 14)
if not pre.exists():
    shutil.copy(p, pre)
W = json.loads(pre.read_text(encoding="utf-8"))
norm = lambda s: re.sub(r"[^a-z0-9']", "", s.lower())
chunks = [l.strip() for l in open(plan, encoding="utf-8") if l.strip()]
i = 0
for c in chunks:
    toks = [norm(t) for t in c.split()]
    got = [norm(W[i + k]["w"]) for k in range(len(toks))]
    assert got == toks, f"chunk {c!r} vs words {[W[i + k]['w'] for k in range(len(toks))]}"
    last = W[i + len(toks) - 1]
    if not re.search(r"[.?!]$", last["w"]):
        last["w"] = re.sub(r"[,;:]$", "", last["w"]) + "."
    i += len(toks)
assert i == len(W), f"plan covers {i} of {len(W)} words; next: {[w['w'] for w in W[i:i+6]]}"
p.write_text(json.dumps(W, indent=1), encoding="utf-8")
L = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}
# the invisible period counts toward width: lock any chunk it pushes over a limit
keep = L.get("keep_together", [])
for c in chunks:
    n, wd = len(c.split()), len(re.sub(r"[,;:]$", "", c)) + (0 if re.search(r"[.?!]$", c) else 1)
    if n > 1 and (wd > max_chars or (n == 4 and wd > four) or n > 4) and norm(c) not in [norm(k) for k in keep]:
        keep.append(" ".join(norm(t) for t in c.split()))
L["keep_together"] = keep
lp.write_text(json.dumps(L, indent=1), encoding="utf-8")
cf = L.get("captions_from", 0)
cf = cf if isinstance(cf, (int, float)) else 0   # "first_cut": check by eye in a preview
speed = reel.render_speed(name)
shown = [w for w in W if w["s"] / speed >= cf - 0.01]
out = [" ".join(x["w"] for x in c) for c in reel.mouthfuls(
    shown, sc.get("min_words", 2), sc.get("max_words", 3), max_chars, four, keep)]
strip = lambda c: re.sub(r"[.,?!]+$", "", c).lower()
hidden = len(chunks) - len(out)
mism = [(a, b) for a, b in zip(out, chunks[hidden:]) if strip(a) != strip(b)]
print(f"{len(chunks)} planned ({hidden} hidden), {len(out)} shown; mismatches: {mism[:8]}")
bad = [c for c in chunks if len(strip(c)) > max_chars or (len(c.split()) >= 4 and len(strip(c)) > four)]
print("over the size rule (deliberate exceptions only):", bad)
