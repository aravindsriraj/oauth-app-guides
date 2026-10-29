"""One guide run: resolve -> plain checks -> agent team -> evidence gate -> link check -> save.
CLI (used to build the library): uv run python -m app.pipeline "Linear" """
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx
from google.cloud import storage
from langchain_core.tracers.langchain import wait_for_all_tracers
from langsmith import Client

from app.agent import MODEL, Ctx, build
from app.guide import (BROKEN, PROBE_TEXT, Claim, Entry, Guide, GuideDraft, apply_links, check_links, claims, gate,
                       probe_authorize, query_for, resolve, slugify)

# ---------- storage (one GCS bucket) ----------

_bucket = None


def bucket():
    """The guides bucket, created on first use so importing this module needs no cloud credentials."""
    global _bucket
    if _bucket is None:
        _bucket = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT")).bucket(os.environ["BUCKET"])
    return _bucket


def save_guide(g: Guide):
    bucket().blob(f"guides/{g.slug}.json").upload_from_string(g.model_dump_json(), content_type="application/json")


def load_guide(slug: str) -> Guide | None:
    b = bucket().blob(f"guides/{slug}.json")
    return Guide.model_validate_json(b.download_as_bytes()) if b.exists() else None


def list_guides() -> list[Guide]:
    """Every saved guide, for the home page."""
    # ponytail: downloads every guide per home-page view; write an index.json when the library nears 100
    return [Guide.model_validate_json(b.download_as_bytes()) for b in bucket().list_blobs(prefix="guides/")]


def upload_shot(src: str, slug: str) -> str:
    """Copy a browser screenshot into our bucket, since the browser service's links may expire. Returns the public URL."""
    data = httpx.get(src, timeout=20).raise_for_status().content
    blob = bucket().blob(f"shots/{slug}/{uuid.uuid4().hex[:8]}.png")
    blob.upload_from_string(data, content_type="image/png")
    return blob.public_url


def take_daily_slot(cap: int) -> bool:
    """Daily live-run counter. Safe without locking because the service runs one instance, one run at a time."""
    b = bucket().blob(f"usage/{datetime.now(timezone.utc):%Y-%m-%d}.txt")
    n = int(b.download_as_text()) if b.exists() else 0
    if n >= cap:
        return False
    b.upload_from_string(str(n + 1))
    return True


# ---------- the run ----------

def share_trace(run_id: uuid.UUID) -> str:
    """Make the run's LangSmith trace public and return its link; empty if tracing is off or sharing fails."""
    try:
        wait_for_all_tracers()
        # ponytail: share_run is deprecated (removed after Jan 2027); runs.share.create needs session + trace ids
        return Client().share_run(run_id)
    except Exception:
        return ""


COMPOSIO_DOCS = "https://docs.composio.dev/toolkits/managed-auth"


def name_and_slug(app_input: str, facts: dict | None) -> tuple[str, str]:
    """The app's display name and the guide's id: Composio's names when it has a toolkit, else the brand from a
    website (strava.com -> Strava) or the name as typed."""
    typed = app_input.strip()
    brand = query_for(typed)
    name = (facts or {}).get("name") or (brand.title() if brand != typed.lower() else typed)
    return name, slugify((facts or {}).get("toolkit") or name)


async def run(app_input: str, emit, facts: dict | None) -> Guide | None:
    """facts: the Composio toolkit details from resolve(), or None if Composio has no toolkit for this app."""
    t0 = time.time()
    # one progress line on the live page
    line = lambda text, tag="", url="", who="checks": emit({"kind": "line", "who": who, "text": text, "tag": tag, "url": url})
    toolkit = facts or {}
    name, slug = name_and_slug(app_input, facts)

    checks, website = await plain_checks(app_input, name, facts, line)
    ctx = Ctx(name=name, facts=toolkit, checks=checks, emit=emit, upload=lambda u: upload_shot(u, slug))
    ctx.seen.update({u: "" for u in [website, toolkit.get("authorize_url", ""), COMPOSIO_DOCS] if u})
    draft, observed, run_id = await research(build(ctx), name, line)
    if draft is None:
        emit({"kind": "error", "text": "The agents did not produce a guide. Try again in a minute."})
        return None

    guide = Guide(**draft.model_dump(), slug=slug, app_name=name, website=website, logo=toolkit.get("logo", ""), composio=toolkit,
                  checks=checks, observed=observed, model=MODEL,
                  screenshots=[Entry(label=page, value=ours) for ours, page in ctx.shots.items()])
    await enforce_evidence(guide, ctx, line)
    guide.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    guide.seconds = int(time.time() - t0)
    guide.trace_url = await asyncio.to_thread(share_trace, run_id)
    await asyncio.to_thread(save_guide, guide)
    emit({"kind": "done", "text": f"Guide ready in {guide.seconds}s", "url": f"/guides/{slug}"})
    return guide


async def enforce_evidence(guide: Guide, ctx: Ctx, line) -> None:
    """The evidence gate, then a check of every cited link."""
    downgraded = gate(guide, ctx.seen, ctx.browsed, ctx.facts.get("redirect_uri", ""))
    line(f"Evidence check: {downgraded} claim(s) downgraded (source not read this run, or a number not on the cited page)")
    async with httpx.AsyncClient() as client:
        urls = {c.source_url for c in claims(guide) if c.source_url} | {s.url for s in guide.steps if s.url}
        guide.links = await check_links(urls, client)
    broken = apply_links(guide, guide.links)
    dead = sum(status in BROKEN for status in guide.links.values())
    line(f"Link check: {len(guide.links)} links, {dead} broken" + (f", {broken} claim(s) marked uncertain" if broken else ""))


async def plain_checks(app_input: str, name: str, facts: dict | None, line) -> tuple[list[Claim], str]:
    """What our own code verifies before any agent runs: the Composio toolkit, the website, the authorize URL."""
    checks: list[Claim] = []

    def check(label, text, url):
        checks.append(Claim(label=label, text=text, tag="tested", source_url=url))
        line(text, "tested", url)

    if facts:
        check("Composio toolkit", f"Composio has a toolkit for {name} (`{facts['toolkit']}`); Composio-managed OAuth app: "
              f"{'yes' if facts['managed'] else 'no'}; auth methods: {', '.join(facts['auth_modes'])}",
              COMPOSIO_DOCS)
    else:
        line(f"No Composio toolkit matched “{app_input}”; researching it from scratch", "uncertain")

    website = website_for(app_input, facts or {})
    async with httpx.AsyncClient(timeout=15) as client:
        if website:
            status = (await check_links({website}, client)).get(website)
            check("Website", f"{website} loads (HTTP {status})" if status and status > 0 else f"{website} did not load", website)
        if facts and facts["authorize_url"]:
            check("Authorize URL", PROBE_TEXT[await probe_authorize(facts, client)], facts["authorize_url"])
    return checks, website


def website_for(app_input: str, facts: dict) -> str:
    """Composio's app_url, else the input itself when it looks like a domain."""
    site = facts.get("app_url") or (app_input.strip() if "." in app_input else "")
    return site if not site or site.startswith("http") else "https://" + site


async def research(agent, name: str, line) -> tuple[GuideDraft | None, list[Claim], uuid.UUID]:
    """Stream the agent team, reporting progress. Returns the draft guide, the browser's observations and the trace id."""
    run_id = uuid.uuid4()
    config = {"run_id": run_id, "run_name": f"guide: {name}", "recursion_limit": 120}
    task_names: dict[str, str] = {}  # task tool_call_id -> specialist
    observed: list[Claim] = []
    draft = None
    line("Orchestrator is briefing three specialists", who="orchestrator")
    try:
        async with asyncio.timeout(540):  # well inside Cloud Run's 900s request timeout
            async for ns, update in agent.astream({"messages": [{"role": "user", "content": f"Write the OAuth app setup guide for {name}."}]},
                                                  config=config, stream_mode="updates", subgraphs=True):
                if ns:  # inside a specialist; its tools report their own progress
                    continue
                for changed in (update or {}).values():  # each update maps a graph node to the state it changed
                    if not isinstance(changed, dict):
                        continue
                    if changed.get("structured_response") is not None:
                        draft = changed["structured_response"]
                    for message in changed.get("messages") or []:
                        observed += on_message(message, task_names, line)
    except TimeoutError:
        line("Research ran out of time", "uncertain", who="orchestrator")
    if isinstance(draft, dict):
        draft = GuideDraft.model_validate(draft)
    return draft, observed, run_id


def on_message(message, task_names: dict[str, str], line) -> list[Claim]:
    """Report the lead writer's briefs and each specialist's findings. Returns the browser observations, if any."""
    for call in getattr(message, "tool_calls", None) or []:  # the lead writer briefing a specialist, or writing the guide
        if call["name"] == "task":
            who = call["args"].get("subagent_type", "specialist").replace("-", " ")
            task_names[call["id"]] = who
            line(f"→ {who}: {call['args'].get('description', '')[:160]}", who="orchestrator")
        elif call["name"] == "GuideDraft":
            line("Assembling the guide", who="orchestrator")
    if getattr(message, "type", "") != "tool" or getattr(message, "name", "") != "task":
        return []
    who = task_names.get(message.tool_call_id, "specialist")  # a specialist's result coming back
    data = report_findings(who, str(message.content), line)
    observed = []
    for item in data.get("observations", []) if who == "portal explorer" else []:
        try:
            observed.append(Claim.model_validate(item))
        except ValueError:  # skip a malformed observation rather than lose the run
            pass
    return observed


def report_findings(who: str, content: str, line):
    """Turn a specialist's JSON result into one progress line per claim."""
    try:
        data = json.loads(content[content.find("{"):content.rfind("}") + 1])
    except ValueError:
        line(f"{who} finished (no structured result)", "uncertain", who=who)
        return {}
    found = []  # every claim in the result, whether a single field or a list
    for value in data.values():
        if isinstance(value, dict) and "tag" in value:
            found.append(value)
        elif isinstance(value, list):
            found += [item for item in value if isinstance(item, dict) and "tag" in item]
    for item in found:
        text = (f"{item['label']}: " if item.get("label") else "") + item.get("text", "")[:140]
        line(text, item.get("tag", ""), item.get("source_url", ""), who=who)
    line(f"{who} finished: {len(found)} findings", who=who)
    return data


if __name__ == "__main__":
    def emit(e):
        tag = f"[{e.get('tag')}] " if e.get("tag") else ""
        print(f"{e.get('who', e['kind']):>20} | {tag}{e.get('text', '')} {e.get('url', '')}", flush=True)
    async def cli(app_input):
        async with httpx.AsyncClient(timeout=15) as client:
            facts = await resolve(app_input, client)
        return await run(app_input, emit, facts)
    g = asyncio.run(cli(" ".join(sys.argv[1:])))
    if g:
        os.makedirs("out", exist_ok=True)
        with open(f"out/{g.slug}.{g.model}.json", "w") as f:  # local copy for comparisons and verification
            f.write(g.model_dump_json(indent=1))
        print(f"saved guides/{g.slug}.json  difficulty={g.difficulty}  trace={g.trace_url}")
