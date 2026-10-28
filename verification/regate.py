"""Re-run the current evidence gate on saved guides, re-reading every cited page, and list any tag it would change.
Usage: uv run python verification/regate.py verification/final-*.json"""
import sys

from composio import Composio

sys.path.insert(0, ".")
from app.guide import Guide, apply_links, claims, gate  # noqa: E402

session = Composio().sessions.create(user_id="oauth-guide-bot", session_preset="direct_tools", toolkits=["composio_search"],
                                     tools={"composio_search": ["COMPOSIO_SEARCH_FETCH_URL_CONTENT"]})


def page(url: str) -> str:
    r = session.execute("COMPOSIO_SEARCH_FETCH_URL_CONTENT", arguments={"urls": [url]})
    return ((r.data or {}).get("results") or [{}])[0].get("text") or ""


for path in sys.argv[1:]:
    g = Guide.model_validate_json(open(path).read())
    before = [(c.text, c.tag) for c in claims(g)]
    seen = {u: page(u) for u in {c.source_url for c in claims(g) if c.tag == "sourced" and c.source_url}}
    seen.update({c.source_url: "" for c in g.checks})
    browsed = {c.source_url for c in claims(g) if c.tag == "tested"}  # the browser opened these during research
    gate(g, seen, browsed, g.composio.get("redirect_uri", ""))
    apply_links(g, g.links)
    after = {c.text: c.tag for c in claims(g)}
    changed = [(t, tag, after.get(t, "removed")) for t, tag in before if after.get(t, "removed") != tag]
    print(f"{path}: {len(before)} claims, {len(changed)} would change")
    for text, was, now in changed:
        print(f"   {was} -> {now}: {text[:110]}")
