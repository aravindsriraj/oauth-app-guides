"""Guide data model, the evidence gate, and the deterministic checks that run without an LLM."""
import asyncio
import os
import re
from typing import Literal
from urllib.parse import urlparse

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
    checked_at: str = ""  # set by code


class Entry(BaseModel):
    label: str
    value: str


class Step(Claim):
    actor: Literal["agent", "you"] = Field(description="agent only if it needs no login to the provider")
    url: str = Field("", description="Direct link to the page where this step happens")
    fields: list[Entry] = Field([], description="Form field -> what to enter")
    screenshot: str = ""


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
    screenshots: list[Entry] = Field(description="label = caption, value = screenshot URL returned by browse")


# ---------- input resolution ----------

SKIP_LABELS = {"www", "app", "api", "developer", "developers", "docs", "com", "io", "dev", "co", "ai", "net", "org"}


def query_for(app_input: str) -> str:
    s = app_input.strip().lower()
    if "/" in s or ("." in s and " " not in s):
        host = urlparse(s if "//" in s else "//" + s).hostname or s
        labels = host.split(".")
        # ponytail: drop the TLD, skip generic labels, take the last one left; a brand living in a subdomain resolves wrong
        kept = [p for p in labels[:-1] if p not in SKIP_LABELS] or labels
        s = kept[-1]
    return s


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def facts(d: dict) -> dict:
    """Composio toolkit details -> the ground truth the guide and agents rely on."""
    modes = [a.get("mode") for a in d.get("auth_config_details") or []]
    oauth = next((a for a in d.get("auth_config_details") or [] if a.get("mode") == "OAUTH2"), {})
    opt = {f["name"]: f.get("default") for f in oauth.get("fields", {}).get("auth_config_creation", {}).get("optional", [])}
    prov = oauth.get("deprecated_auth_provider_details") or {}
    managed = d.get("composio_managed_auth_schemes") or []
    scopes = next((m.get("scopes", {}).get("available", []) for m in d.get("composio_managed_auth") or [] if m.get("mode") == "OAUTH2"), [])
    return {
        "toolkit": d["slug"], "name": d.get("name", d["slug"]), "auth_modes": modes, "managed": "OAUTH2" in managed,
        "managed_scopes": scopes, "redirect_uri": opt.get("oauth_redirect_uri") or "",
        "default_scopes": [s for s in (opt.get("scopes") or "").split(",") if s],
        "authorize_url": prov.get("authorization_url", ""), "token_url": prov.get("token_url", ""),
        "app_url": (d.get("meta") or {}).get("app_url", ""), "logo": (d.get("meta") or {}).get("logo", ""),
    }


async def resolve(app_input: str, client: httpx.AsyncClient) -> dict | None:
    """Find the Composio toolkit for a name or URL. None if Composio has no matching toolkit."""
    q = query_for(app_input)
    h = {"x-api-key": os.environ["COMPOSIO_API_KEY"]}
    r = await client.get(f"{API}/v3/toolkits", params={"search": q, "limit": 10}, headers=h)
    items = r.json().get("items", []) if r.is_success else []
    flat = q.replace(" ", "")
    pick = next((i for i in items if i["slug"] == flat or i["name"].lower() == q), None) or \
        next((i for i in items if flat in i["slug"] or i["slug"] in flat), None)
    if not pick:
        return None
    r = await client.get(f"{API}/v3.1/toolkits/{pick['slug']}", headers=h)
    return facts(r.json()) if r.is_success else None


def verdict(c: dict, app_name: str) -> str:
    if not c:
        return f"Composio has no toolkit for {app_name} yet, so there is no managed app to fall back on."
    if "OAUTH2" not in c["auth_modes"]:
        return f"Composio connects {app_name} with {', '.join(c['auth_modes']) or 'no auth'}, not OAuth, so no OAuth app is needed."
    if c["managed"]:
        return f"No, not to get started: Composio provides an OAuth app for {app_name}, so users can connect today. You need your own app for the reasons below."
    return f"Yes: Composio doesn't provide an OAuth app for {app_name}. You must create one before any user can connect."


# ---------- probes (plain HTTP, our own code) ----------

def classify_probe(status: int, final_url: str, body: str) -> str:
    text = re.sub(r"<[^>]+>", " ", body[:20000])
    if re.search(r"invalid[_ ]client|client (id )?(was )?not found|unknown client|unauthorized[_ ]client|unauthorized access|invalid client_id", text, re.I):
        return "rejects_fake_client"
    if status in (404, 410):
        return "not_found"
    if status in (403, 429, 999) or re.search(r"captcha|cloudflare|access denied", text, re.I):
        return "blocked"
    if status in (400, 401):
        return "rejects_fake_client"
    if re.search(r"/(login|signin|sign-in|sign_in)\b", urlparse(final_url).path, re.I):
        return "login_wall"
    return "responds" if status < 400 else "error"


PROBE_TEXT = {
    "rejects_fake_client": "Authorize URL is live and rejects an unregistered client_id, so you need a real registered app",
    "responds": "Authorize URL is live (it renders a page; we couldn't confirm how it treats unknown apps)",
    "login_wall": "Authorize URL is live and sends visitors to a login page first",
    "not_found": "Authorize URL returned 404; the OAuth endpoint may have moved",
    "blocked": "Authorize URL blocked our automated check (bot protection)",
    "error": "Authorize URL returned an error we couldn't classify",
}


async def probe_authorize(c: dict, client: httpx.AsyncClient) -> tuple[str, str]:
    params = {"client_id": "composio-guide-probe", "redirect_uri": c["redirect_uri"], "response_type": "code"}
    try:
        r = await client.get(c["authorize_url"], params=params, follow_redirects=True, timeout=10, headers=UA)
        return classify_probe(r.status_code, str(r.url), r.text), str(r.request.url)
    except httpx.HTTPError:
        return "error", c["authorize_url"]


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
    p = urlparse(u.strip())
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


def claims(d: GuideDraft):
    yield d.dev_time
    yield d.launch_time
    yield d.app_type
    for group in (d.own_app_reasons, d.prerequisites, d.steps, d.scopes, d.approval, d.pitfalls, d.support,
                  getattr(d, "observed", [])):
        yield from group


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_on_page(c: Claim, page: str) -> bool:
    page = page.replace(",", "")
    return all(x.replace(",", "") in page for x in NUMBER.findall(f"{c.label} {c.text}"))


SCOPE = re.compile(r"https?://\S+|[\w-]+(?:[.:][\w*-]+)+")


def scope_on_page(s: "Scope", page: str) -> bool:
    """A scope claim is backed only if one of its scope strings (crm.objects.contacts.read, https://mail.google.com/) is on the page."""
    return any(t.rstrip(".,;)") in page for t in SCOPE.findall(f"{s.label} {s.text}"))


QUOTA = re.compile(r"rate.?limit|quota", re.I)
UNIT = r"(app|application|client|project)"


def per_app_limits(c: Claim, page: str) -> bool:
    """True if the claim says limits are counted per app/client/project and the page says the same."""
    m = re.search(rf"\bper (?:\w+ )?{UNIT}\b|\b{UNIT}[- ]level\b", c.text, re.I)
    unit = m and (m.group(1) or m.group(2)).lower()
    return bool(unit) and bool(re.search(rf"\bper (?:\w+ )?{unit}\b|\b{unit}[- ]level\b", page, re.I))


def gate(d: GuideDraft, seen: dict[str, str], browsed: set[str], shots: dict[str, str], redirect_uri: str) -> int:
    """Enforce the evidence rules in place; returns how many claims were downgraded.
    seen maps each url the agents read this run to the text they saw there;
    shots maps each screenshot we uploaded this run to the page it shows."""
    seen, browsed = {norm(u): t for u, t in seen.items()}, {norm(u) for u in browsed}
    steps = {id(s) for s in d.steps}
    n = 0
    for c in claims(d):
        before = c.tag
        if not c.source_url.startswith(("http://", "https://")):
            c.source_url = ""
        u = norm(c.source_url) if c.source_url else ""
        # a step is never "tested": agents never log in, and every setup step happens behind the provider's login
        if c.tag == "tested" and (u not in browsed or id(c) in steps):
            c.tag = "sourced"
        if c.tag == "sourced" and (not u or (u not in seen and u not in browsed)):
            c.tag, c.source_url = "uncertain", ""
        # a sourced number must appear on the page the agent read, or it was made up or misattributed
        if c.tag == "sourced" and not numbers_on_page(c, seen.get(u, "")):
            c.tag = "uncertain"
        # likewise a scope's review level is only sourced if the page actually lists that scope
        if c.tag == "sourced" and isinstance(c, Scope) and not scope_on_page(c, seen.get(u, "")):
            c.tag = "uncertain"
        n += c.tag != before
    # "own app = own rate limits" was the most common wrong claim in the audit: keep it only when the page backs it
    kept = [c for c in d.own_app_reasons
            if not QUOTA.search(f"{c.label} {c.text}") or per_app_limits(c, seen.get(norm(c.source_url), ""))]
    n += len(d.own_app_reasons) - len(kept)
    d.own_app_reasons = kept
    for s in d.steps:
        if not s.url.startswith(("http://", "https://")):
            s.url = ""
        if s.screenshot not in shots or not s.url or norm(shots[s.screenshot]) != norm(s.url):
            s.screenshot = ""  # only keep a screenshot that shows this step's own page
        for f in s.fields:
            if "redirect" in f.label.lower() and redirect_uri:
                f.value = redirect_uri
    return n


BROKEN = {0, 404, 410}


def apply_links(d: GuideDraft, links: dict[str, int]) -> int:
    n = 0
    for c in claims(d):
        if c.source_url and links.get(c.source_url) in BROKEN and c.tag != "uncertain":
            c.tag, n = "uncertain", n + 1
    return n
