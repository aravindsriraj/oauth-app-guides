"""Self-check for the deterministic logic: uv run python -m app.check"""
from app.guide import Claim, Entry, GuideDraft, Step, apply_links, classify_probe, gate, query_for, slugify

# input -> toolkit search query
assert query_for("https://www.hubspot.com/") == "hubspot"
assert query_for("linear.app") == "linear"
assert query_for("app.hubspot.com/oauth") == "hubspot"
assert query_for("Google Sheets") == "google sheets"
assert slugify("Google Sheets!") == "google-sheets"

# authorize-URL probe, using responses seen live on 2026-10-28
assert classify_probe(200, "https://accounts.google.com/signin/oauth/error?authError=x", "Error 401: invalid_client") == "rejects_fake_client"
assert classify_probe(401, "https://oauth.pipedrive.com/oauth/authorize", '{"error":"unauthorized access"}') == "rejects_fake_client"
assert classify_probe(200, "https://linear.app/oauth/authorize?client_id=x", "Linear Loading…") == "responds"
assert classify_probe(404, "https://x.com/oauth", "Not Found") == "not_found"
assert classify_probe(403, "https://x.com/oauth", "Attention Required! | Cloudflare") == "blocked"
assert classify_probe(200, "https://x.com/login?next=/oauth", "Sign in to continue") == "login_wall"

# evidence gate
def draft(**kw):
    c = lambda tag, url="": Claim(text="t", tag=tag, source_url=url)
    base = dict(difficulty="easy", difficulty_why="", dev_time=c("sourced"), launch_time=c("sourced"), flags=[],
                own_app_reasons=[], app_type=c("sourced"), prerequisites=[], steps=[], scopes=[], approval=[],
                pitfalls=[], support=[], email_subject="", email_body="")
    return GuideDraft(**(base | kw))

d = draft(prerequisites=[
    Claim(text="a", tag="tested", source_url="https://x.com/signup/"),     # browsed (trailing slash ignored) -> stays tested
    Claim(text="b", tag="tested", source_url="https://docs.x.com/a#frag"),  # seen, not browsed -> sourced
    Claim(text="c", tag="sourced", source_url="https://made-up.com/page"),  # never seen -> uncertain, url cleared
    Claim(text="d", tag="sourced", source_url="javascript:alert(1)"),       # bad scheme -> uncertain, url cleared
    Claim(text="e", tag="uncertain"),
], steps=[Step(text="s", tag="sourced", source_url="https://docs.x.com/a", actor="you",
               screenshot="https://evil.com/x.png", fields=[Entry(label="Redirect URL", value="https://wrong")])])
n = gate(d, seen={"https://docs.x.com/a", "https://x.com/signup"}, browsed={"https://x.com/signup"},
         shots=set(), redirect_uri="https://backend.composio.dev/api/v1/auth-apps/add")
tags = [p.tag for p in d.prerequisites]
assert tags == ["tested", "sourced", "uncertain", "uncertain", "uncertain"], tags
assert d.prerequisites[2].source_url == "" and d.prerequisites[3].source_url == ""
assert d.dev_time.tag == "uncertain"          # sourced with no url at all
assert d.steps[0].tag == "sourced" and d.steps[0].screenshot == ""
assert d.steps[0].fields[0].value == "https://backend.composio.dev/api/v1/auth-apps/add"
assert n == 6, n                              # b, c, d, dev_time, launch_time, app_type

# broken source after link check -> uncertain; blocked (403) stays
d = draft(pitfalls=[Claim(text="p", tag="sourced", source_url="https://docs.x.com/gone"),
                    Claim(text="q", tag="sourced", source_url="https://docs.x.com/blocked")])
assert apply_links(d, {"https://docs.x.com/gone": 404, "https://docs.x.com/blocked": 403}) == 1
assert [p.tag for p in d.pitfalls] == ["uncertain", "sourced"]

print("ok")
