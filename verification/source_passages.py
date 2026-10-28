"""Print, for each sourced claim in a saved guide, the best-matching passage from its cited page.
Usage: uv run python verification/source_passages.py verification/final-linear.json"""
import json, re, sys
from composio import Composio
sys.path.insert(0, ".")
from app.guide import Guide, claims
g = Guide.model_validate_json(open(sys.argv[1]).read())
s = Composio().sessions.create(user_id="oauth-guide-bot", session_preset="direct_tools", toolkits=["composio_search"],
    tools={"composio_search": ["COMPOSIO_SEARCH_FETCH_URL_CONTENT"]})
pages = {}
for u in sorted({c.source_url for c in claims(g) if c.source_url}):
    r = s.execute("COMPOSIO_SEARCH_FETCH_URL_CONTENT", arguments={"urls": [u]})
    pages[u] = ((r.data or {}).get("results") or [{}])[0].get("text") or ""
words = lambda t: {w for w in re.findall(r"[a-z0-9:+.-]{4,}", t.lower())}
for i, c in enumerate(claims(g)):
    if c.tag == "uncertain" or not c.source_url: continue
    sents = re.split(r"(?<=[.!?])\s+|\n+", pages.get(c.source_url, ""))
    best = sorted(sents, key=lambda x: -len(words(x) & words(c.text)))[:2]
    print(f"#{i} [{c.tag}] {c.label}: {c.text[:220]}\n   SRC {c.source_url}\n   > " + " | ".join(b.strip()[:260] for b in best) + "\n")
print("UNCERTAIN:", [c.text[:120] for c in claims(g) if c.tag == "uncertain"])
