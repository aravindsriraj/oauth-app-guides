"""Guide data model, the evidence gate, and the deterministic checks that run without an LLM."""
import asyncio
import os
import re
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, Field

Tag = Literal["tested", "sourced", "uncertain"]
API = "https://backend.composio.dev/api"
UA = {"User-Agent": "Mozilla/5.0 (compatible; oauth-guide-bot/1.0)"}


class Claim(BaseModel):
    label: str = Field("", description="Short label, e.g. 'Verified domain', 'Typical wait'")
    text: str
    tag: Tag = Field(description="tested = observed by the browser this run; sourced = stated on source_url; uncertain = inferred or unsourced")
    source_url: str = Field("", description="Exact URL fetched or cited this run; empty if none")


class Entry(BaseModel):
    label: str
    value: str


class Step(Claim):
    actor: Literal["agent", "you"] = Field(description="agent only if it needs no login to the provider")
    url: str = Field("", description="Direct link to the page where this step happens")
    fields: list[Entry] = Field([], description="Form field -> what to enter")


class Scope(Claim):
    review: Literal["none", "review", "restricted", "unknown"]


class GuideDraft(BaseModel):
    difficulty: Literal["easy", "medium", "hard"]
    difficulty_why: str
    dev_time: Claim = Field(description="label = short range like '15-30 min'; text = what that time covers, until the developer has a working app for their own testing")
    launch_time: Claim = Field(description="label = short range like 'Same day' or '4-8 weeks'; text = what that covers, until any customer can connect, including review waits")
    flags: list[str] = Field(description="Short warnings, e.g. 'Security assessment', 'Paid plan required'")
    own_app_reasons: list[Claim] = Field(description="Reasons a developer would need their own OAuth app for this provider")
    app_type: Claim = Field(description="Which kind of app to create on the provider's platform and why")
    prerequisites: list[Claim]
    steps: list[Step]
    scopes: list[Scope]
    approval: list[Claim] = Field(description="Labels: When required, What's checked, Wait, Cost, Common rejections, Before approval")
    pitfalls: list[Claim]
    support: list[Claim] = Field(description="Where to get help from the provider")
    email_subject: str
    email_body: str


class Guide(GuideDraft):
    slug: str
    app_name: str
    website: str = ""
    logo: str = ""
    composio: dict = {}
    checks: list[Claim] = []
    observed: list[Claim] = []     # portal explorer's browser observations, copied in by code
    links: dict[str, int] = {}
    screenshots: list[Entry] = []  # label = page shown, value = our image url
    generated_at: str = ""
    model: str = ""
    seconds: int = 0
    trace_url: str = ""


class SetupFindings(BaseModel):
    portal_url: str
    app_type: Claim
    prerequisites: list[Claim]
    steps: list[Step]
    pitfalls: list[Claim]
    support: list[Claim]


class ApprovalFindings(BaseModel):
    install_cap: Claim = Field(description="Most accounts, users or workspaces that can install before review or listing; 'No cap found' if none")
    unreviewed_limits: Claim = Field(description="Lower API limits or terms restrictions for apps that are not reviewed or listed; 'None found' if none")
    scopes: list[Scope]
    approval: list[Claim]
    pitfalls: list[Claim]


class PortalFindings(BaseModel):
    observations: list[Claim] = Field(description="What the browser itself saw happen: a form's fields, a login wall, a card field, a plan gate. Tag tested.")
    stated: list[Claim] = Field(description="What the pages say (policies, requirements, docs text). Tag sourced, never tested.")


# ---------- input resolution ----------

SKIP_LABELS = {"www", "app", "api", "developer", "developers", "docs", "com", "io", "dev", "co", "ai", "net", "org"}


def query_for(app_input: str) -> str:
    """The search term for Composio's toolkit search: a name as typed, or the brand label of a URL (app.hubspot.com -> hubspot)."""
    s = app_input.strip().lower()
    if "/" in s or ("." in s and " " not in s):
        host = urlparse(s if "//" in s else "//" + s).hostname or s
        labels = host.split(".")
        # ponytail: drop the TLD, skip generic labels, take the last one left; a brand living in a subdomain resolves wrong
        kept = [p for p in labels[:-1] if p not in SKIP_LABELS] or labels
        s = kept[-1]
    return s


def slugify(s: str) -> str:
    """URL-safe id for a guide: "Google Sheets" -> "google-sheets"."""
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def toolkit_facts(toolkit: dict) -> dict:
    """Composio toolkit details -> the ground truth the guide and agents rely on."""
    configs = toolkit.get("auth_config_details") or []
    oauth = next((a for a in configs if a.get("mode") == "OAUTH2"), {})
    defaults = {f["name"]: f.get("default") or "" for f in oauth.get("fields", {}).get("auth_config_creation", {}).get("optional", [])}
    provider = oauth.get("deprecated_auth_provider_details") or {}
    meta = toolkit.get("meta") or {}
    return {
        "toolkit": toolkit["slug"], "name": toolkit.get("name", toolkit["slug"]), "auth_modes": [a.get("mode") for a in configs],
        "managed": "OAUTH2" in (toolkit.get("composio_managed_auth_schemes") or []),
        "redirect_uri": defaults.get("oauth_redirect_uri", ""),
        "default_scopes": [x for x in defaults.get("scopes", "").split(",") if x],
        "authorize_url": provider.get("authorization_url", ""), "token_url": provider.get("token_url", ""),
        "app_url": meta.get("app_url", ""), "logo": meta.get("logo", ""),
    }


def pick_toolkit(items: list[dict], q: str) -> dict | None:
    """An exact slug or name match, else a slug that contains the query (or vice versa). Never just the first hit."""
    flat = q.replace(" ", "")
    return next((i for i in items if i["slug"] == flat or i["name"].lower() == q), None) or \
        next((i for i in items if flat in i["slug"] or i["slug"] in flat), None)


async def resolve(app_input: str, client: httpx.AsyncClient) -> dict | None:
    """Find the Composio toolkit for a name or URL. None if Composio has no matching toolkit."""
    q = query_for(app_input)
    h = {"x-api-key": os.environ["COMPOSIO_API_KEY"]}
    r = await client.get(f"{API}/v3/toolkits", params={"search": q, "limit": 10}, headers=h)
    pick = pick_toolkit(r.json().get("items", []) if r.is_success else [], q)
    if not pick:
        return None
    r = await client.get(f"{API}/v3.1/toolkits/{pick['slug']}", headers=h)
    return toolkit_facts(r.json()) if r.is_success else None


def verdict(facts: dict, app_name: str) -> str:
    """The answer to "Do you need your own app?", decided from Composio's facts rather than by the model."""
    if not facts:
        return f"Composio has no toolkit for {app_name} yet, so there is no managed app to fall back on."
    if "OAUTH2" not in facts["auth_modes"]:
        return f"Composio connects {app_name} with {', '.join(facts['auth_modes']) or 'no auth'}, not OAuth, so no OAuth app is needed."
    if facts["managed"]:
        return f"No, not to get started: Composio provides an OAuth app for {app_name}, so users can connect today. You need your own app for the reasons below."
    return f"Yes: Composio doesn't provide an OAuth app for {app_name}. You must create one before any user can connect."


# ---------- probes (plain HTTP, our own code) ----------

def classify_probe(status: int, final_url: str, body: str) -> str:
    """What the authorize URL did with our made-up client_id: one of the PROBE_TEXT keys."""
    text = re.sub(r"<[^>]+>", " ", body)  # the whole page: Google puts its error text after ~20k chars of script
    if re.search(r"invalid[_ ]client|client (id )?(was )?not found|unknown client|unauthorized[_ ]client|unauthorized access|invalid client_id", text, re.I):
        return "rejects_fake_client"
    if status in (403, 429, 999) or re.search(r"captcha|cloudflare|access denied", text, re.I):
        return "blocked"
    if status in (400, 401):
        return "rejects_fake_client"
    return "responds" if status < 400 else "error"


PROBE_TEXT = {
    "rejects_fake_client": "Authorize URL is live and rejects an unregistered client_id, so you need a real registered app",
    "responds": "Authorize URL is live (it renders a page; we couldn't confirm how it treats unknown apps)",
    "blocked": "Authorize URL blocked our automated check (bot protection)",
    "error": "Authorize URL returned an error (it may have moved); check it before relying on it",
}


async def probe_authorize(facts: dict, client: httpx.AsyncClient) -> str:
    """Call the provider's authorize URL with a made-up client_id and classify the response."""
    params = {"client_id": "composio-guide-probe", "redirect_uri": facts["redirect_uri"], "response_type": "code",
              "scope": " ".join(facts["default_scopes"])}  # some providers (Google) reject a request with no scope before checking the client
    try:
        r = await client.get(facts["authorize_url"], params=params, follow_redirects=True, timeout=10, headers=UA)
        return classify_probe(r.status_code, str(r.url), r.text)
    except httpx.HTTPError:
        return "error"


def icon_links(html: str, base: str) -> list[str]:
    """The icons a page declares, best first: apple-touch-icon (usually 180px), then larger sizes, then /favicon.ico."""
    found = []
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        rel = re.search(r"""\brel\s*=\s*["']?([^"'>]+)""", tag, re.I)
        href = re.search(r"""\bhref\s*=\s*["']?([^"'\s>]+)""", tag, re.I)
        if not (rel and href) or "icon" not in rel.group(1).lower() or "mask" in rel.group(1).lower():
            continue  # mask-icon is a one-color outline, not the app's icon
        size = re.search(r"""\bsizes\s*=\s*["']?(\d+)""", tag, re.I)
        found.append(("apple" not in rel.group(1).lower(), -int(size.group(1)) if size else 0, urljoin(base, href.group(1))))
    return [url for *_, url in sorted(found)] + [urljoin(base, "/favicon.ico")]


BROKEN = {0, 404, 410}  # statuses check_links reports for a page that is gone; 403/429 only mean we were blocked


async def check_links(urls: set[str], client: httpx.AsyncClient) -> dict[str, int]:
    """url -> HTTP status; 0 = could not connect (DNS/refused), -1 = timed out."""
    sem = asyncio.Semaphore(8)

    async def one(u):
        async with sem:
            try:
                return u, (await client.get(u, follow_redirects=True, timeout=8, headers=UA)).status_code
            except httpx.TimeoutException:
                return u, -1
            except httpx.HTTPError:
                return u, 0
    return dict(await asyncio.gather(*(one(u) for u in urls if u.startswith(("http://", "https://")))))


# ---------- evidence gate ----------

def norm(u: str) -> str:
    """Compare URLs loosely: case-insensitive host, no trailing slash, no #fragment."""
    p = urlparse(u.strip())
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


def claims(guide: GuideDraft):
    """Every evidence-carrying claim in a guide, in page order."""
    yield guide.dev_time
    yield guide.launch_time
    yield guide.app_type
    for group in (guide.own_app_reasons, guide.prerequisites, guide.steps, guide.scopes, guide.approval, guide.pitfalls,
                  guide.support, getattr(guide, "observed", [])):
        yield from group


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_on_page(c: Claim, page: str) -> bool:
    """Every number in the claim (5,000 / 24 / 2.0) appears on the page. Digits only: "six months" won't match "6"."""
    page = page.replace(",", "")
    return all(x.replace(",", "") in page for x in NUMBER.findall(f"{c.label} {c.text}"))


SCOPE = re.compile(r"https?://\S+|[\w-]+(?:[.:][\w*-]+)+")


def scope_on_page(s: Scope, page: str) -> bool:
    """A scope claim is backed only if one of its scope strings (crm.objects.contacts.read, https://mail.google.com/) is on the page."""
    return any(t.rstrip(".,;)") in page for t in SCOPE.findall(f"{s.label} {s.text}"))


QUOTA = re.compile(r"rate.?limit|quota", re.I)
UNIT = r"(app|application|client|project)"


def per_app_limits(c: Claim, page: str) -> bool:
    """True if the claim says limits are counted per app/client/project and the page says the same."""
    m = re.search(rf"\bper (?:\w+ )?{UNIT}\b|\b{UNIT}[- ]level\b", c.text, re.I)
    unit = m and (m.group(1) or m.group(2)).lower()
    return bool(unit) and bool(re.search(rf"\bper (?:\w+ )?{unit}\b|\b{unit}[- ]level\b", page, re.I))


def gate(guide: GuideDraft, seen: dict[str, str], browsed: set[str], redirect_uri: str) -> int:
    """Enforce the evidence rules in place; returns how many claims were downgraded or dropped.
    seen maps each url the agents read this run to the text they saw there."""
    seen, browsed = {norm(u): t for u, t in seen.items()}, {norm(u) for u in browsed}
    steps = {id(s) for s in guide.steps}
    n = sum(downgrade(c, seen, browsed, is_step=id(c) in steps) for c in claims(guide))
    n += drop_unbacked_quota_reasons(guide, seen)
    for s in guide.steps:
        clean_step(s, redirect_uri)
    return n


def downgrade(c: Claim, seen: dict[str, str], browsed: set[str], is_step: bool) -> bool:
    """Apply the tag rules to one claim; True if its tag changed."""
    before = c.tag
    if not c.source_url.startswith(("http://", "https://")):
        c.source_url = ""
    u = norm(c.source_url) if c.source_url else ""
    # a step is never "tested": agents never log in, and every setup step happens behind the provider's login
    if c.tag == "tested" and (is_step or u not in browsed):
        c.tag = "sourced"
    if c.tag == "sourced" and (not u or (u not in seen and u not in browsed)):
        c.tag, c.source_url = "uncertain", ""
    if c.tag == "sourced" and not backed_by_page(c, seen.get(u, "")):
        c.tag = "uncertain"
    return c.tag != before


def backed_by_page(c: Claim, page: str) -> bool:
    """Its numbers must be on the page the agent read, and a scope's review level needs the scope on that page."""
    return numbers_on_page(c, page) and (not isinstance(c, Scope) or scope_on_page(c, page))


def drop_unbacked_quota_reasons(guide: GuideDraft, seen: dict[str, str]) -> int:
    """'Own app = own rate limits' was the most common wrong claim in the audit: keep it only when the page backs it."""
    kept = [c for c in guide.own_app_reasons
            if not QUOTA.search(f"{c.label} {c.text}") or per_app_limits(c, seen.get(norm(c.source_url), ""))]
    dropped, guide.own_app_reasons = len(guide.own_app_reasons) - len(kept), kept
    return dropped


def clean_step(s: Step, redirect_uri: str) -> None:
    """Steps link only to http(s) pages, and any redirect field gets Composio's exact redirect URI."""
    if not s.url.startswith(("http://", "https://")):
        s.url = ""
    for f in s.fields:
        if "redirect" in f.label.lower() and redirect_uri:
            f.value = redirect_uri


def apply_links(guide: GuideDraft, links: dict[str, int]) -> int:
    """A claim whose cited page is gone becomes uncertain; returns how many changed."""
    n = 0
    for c in claims(guide):
        if c.source_url and links.get(c.source_url) in BROKEN and c.tag != "uncertain":
            c.tag, n = "uncertain", n + 1
    return n
