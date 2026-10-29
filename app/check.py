"""Self-check for the deterministic logic: uv run python -m app.check"""
from app.guide import Claim, Entry, Guide, GuideDraft, Scope, Step, apply_links, classify_probe, gate, query_for, slugify
from app.main import T
from app.pipeline import name_and_slug

# input -> toolkit search query
assert query_for("https://www.hubspot.com/") == "hubspot"
assert query_for("linear.app") == "linear"
assert query_for("app.hubspot.com/oauth") == "hubspot"
assert query_for("Google Sheets") == "google sheets"
assert slugify("Google Sheets!") == "google-sheets"
assert name_and_slug("  Acme Widgets ", None) == ("Acme Widgets", "acme-widgets")
assert name_and_slug("https://www.strava.com/", None) == ("Strava", "strava")  # a website with no toolkit is named by its brand
assert name_and_slug("linear.app", {"name": "Linear", "toolkit": "linear"}) == ("Linear", "linear")

# authorize-URL probe, using responses seen live on 2026-10-28
assert classify_probe(200, "https://accounts.google.com/signin/oauth/error?authError=x", "Error 401: invalid_client") == "rejects_fake_client"
assert classify_probe(401, "https://oauth.pipedrive.com/oauth/authorize", '{"error":"unauthorized access"}') == "rejects_fake_client"
assert classify_probe(200, "https://linear.app/oauth/authorize?client_id=x", "Linear Loading…") == "responds"
assert classify_probe(404, "https://x.com/oauth", "Not Found") == "error"  # a dead endpoint is just an error we report
assert classify_probe(403, "https://x.com/oauth", "Attention Required! | Cloudflare") == "blocked"
# Google's error text can sit after 20k+ characters of script, so the whole page is read
assert classify_probe(200, "https://accounts.google.com/signin/oauth/error", "<script>" + "x" * 30000 + "</script>Error 401: invalid_client") == "rejects_fake_client"
# an error page we can't read is "responds", never a login wall, even under /signin/
assert classify_probe(200, "https://accounts.google.com/signin/oauth/error?authError=x", "Error 400 (Bad Request)") == "responds"

# evidence gate
def draft(**kw):
    """A minimal guide draft; pass only the sections a test needs."""
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
               fields=[Entry(label="Redirect URL", value="https://wrong")]),
          Step(text="open settings", tag="tested", source_url="https://x.com/signup", actor="you",  # steps happen behind login
               url="https://x.com/settings/api")])
n = gate(d, seen={"https://docs.x.com/a": "", "https://x.com/signup": ""}, browsed={"https://x.com/signup"},
         redirect_uri="https://backend.composio.dev/api/v1/auth-apps/add")
tags = [p.tag for p in d.prerequisites]
assert tags == ["tested", "sourced", "uncertain", "uncertain", "uncertain"], tags
assert d.prerequisites[2].source_url == "" and d.prerequisites[3].source_url == ""
assert d.dev_time.tag == "uncertain"          # sourced with no url at all
assert d.steps[0].tag == "sourced"
assert d.steps[0].fields[0].value == "https://backend.composio.dev/api/v1/auth-apps/add"
assert d.steps[1].tag == "sourced"
assert n == 7, n                              # b, c, d, dev_time, launch_time, app_type, step 1

# broken source after link check -> uncertain; blocked (403) stays
d = draft(pitfalls=[Claim(text="p", tag="sourced", source_url="https://docs.x.com/gone"),
                    Claim(text="q", tag="sourced", source_url="https://docs.x.com/blocked")])
assert apply_links(d, {"https://docs.x.com/gone": 404, "https://docs.x.com/blocked": 403}) == 1
assert [p.tag for p in d.pitfalls] == ["uncertain", "sourced"]

# browser observations on the Guide go through the same gate
g = Guide(**draft().model_dump(), slug="x", app_name="X", observed=[
    Claim(text="login wall", tag="tested", source_url="https://x.com/signup"),
    Claim(text="read in docs", tag="tested", source_url="https://docs.x.com/a")])
gate(g, seen={"https://docs.x.com/a": ""}, browsed={"https://x.com/signup"}, redirect_uri="")
assert [o.tag for o in g.observed] == ["tested", "sourced"]

# a sourced number must appear on the page the agent read
d = draft(approval=[Claim(text="Capped at 100 users", tag="sourced", source_url="https://docs.x.com/caps"),
                    Claim(text="Up to 5,000 requests", tag="sourced", source_url="https://docs.x.com/limits")])
gate(d, seen={"https://docs.x.com/caps": "unverified apps have a user cap", "https://docs.x.com/limits": "OAuth app | 5,000 | hour"},
     browsed=set(), redirect_uri="")
assert [a.tag for a in d.approval] == ["uncertain", "sourced"]

# a sourced scope claim needs its scope string on the cited page
d = draft(scopes=[Scope(label="crm.objects.contacts.read", text="Read contacts", tag="sourced", review="none", source_url="https://h.com/overview"),
                  Scope(label="Gmail full access", text="https://mail.google.com/ is restricted", tag="sourced", review="restricted", source_url="https://g.com/restricted")])
gate(d, seen={"https://h.com/overview": "Scopes provide access to endpoints", "https://g.com/restricted": "restricted scopes include https://mail.google.com/"},
     browsed=set(), redirect_uri="")
assert [s.tag for s in d.scopes] == ["uncertain", "sourced"]

# "own app = own rate limits" survives only if the cited page counts limits per app/client/project
d = draft(own_app_reasons=[
    Claim(text="Your own client allocation rather than sharing rate limits", tag="sourced", source_url="https://l.app/limits"),
    Claim(text="Quota is counted per project, so you get your own", tag="sourced", source_url="https://g.com/quota"),
    Claim(text="Your own name on the consent screen", tag="sourced", source_url="https://g.com/quota")])
gate(d, seen={"https://l.app/limits": "OAuth App | 5,000 | User", "https://g.com/quota": "Per minute per project: units"},
     browsed=set(), redirect_uri="")
assert [c.text[:5] for c in d.own_app_reasons] == ["Quota", "Your "], d.own_app_reasons

# markdown version of a guide renders all ten sections
g = Guide(**draft().model_dump(), slug="x", app_name="X", composio={
    "toolkit": "x", "auth_modes": ["OAUTH2"], "managed": False, "redirect_uri": "https://backend.composio.dev/api/v1/auth-apps/add"})
md = T.get_template("guide.md").render(g=g)
heads = [l for l in md.splitlines() if l.startswith("## ")]
assert heads == ["## Summary", "## Do you need your own app?", "## Before you start", "## Create the app", "## Permissions",
                 "## Getting approved", "## Pitfalls", "## When you're stuck", "## Connect it to Composio", "## Sources and gaps"], heads
assert "Yes: Composio doesn't provide an OAuth app for X" in md

print("ok")
