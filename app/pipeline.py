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

from app.agent import ORCH_MODEL, Ctx, build
from app.guide import (PROBE_TEXT, Claim, Entry, Guide, GuideDraft, apply_links, check_links, claims, gate, probe_authorize,
                       resolve, slugify)

# ---------- storage (one GCS bucket) ----------

_bucket = None


def bucket():
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
    # ponytail: downloads every guide per home-page view; write an index.json when the library nears 100
    return [Guide.model_validate_json(b.download_as_bytes()) for b in bucket().list_blobs(prefix="guides/")]


def upload_shot(src: str, slug: str) -> str:
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
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers
        from langsmith import Client
        wait_for_all_tracers()
        # ponytail: share_run is deprecated (removed after Jan 2027); runs.share.create needs session + trace ids
        return Client().share_run(run_id)
    except Exception:
        return ""


async def run(app_input: str, emit, facts: dict | None = None, resolved: bool = False) -> Guide | None:
    t0 = time.time()
    line = lambda text, tag="", url="", who="checks": emit({"kind": "line", "who": who, "text": text, "tag": tag, "url": url})
    async with httpx.AsyncClient(timeout=15) as client:
        if not resolved:
            facts = await resolve(app_input, client)
        name = facts["name"] if facts else app_input.strip()
        slug = slugify(facts["toolkit"] if facts else name)
        checks: list[Claim] = []

        def check(label, text, url):
            checks.append(Claim(label=label, text=text, tag="tested", source_url=url))
            line(text, "tested", url)

        if facts:
            check("Composio toolkit", f"Composio has a toolkit for {name} (`{facts['toolkit']}`); Composio-managed OAuth app: "
                  f"{'yes' if facts['managed'] else 'no'}; auth methods: {', '.join(facts['auth_modes'])}",
                  "https://docs.composio.dev/toolkits/managed-auth")
        else:
            line(f"No Composio toolkit matched “{app_input}”; researching it from scratch", "uncertain")

        website = (facts or {}).get("app_url") or (app_input if "." in app_input else "")
        if website and not website.startswith("http"):
            website = "https://" + website
        jobs = []
        if website:
            jobs.append(check_links({website}, client))
        if facts and facts["authorize_url"]:
            jobs.append(probe_authorize(facts, client))
        results = await asyncio.gather(*jobs)
        if website:
            status = results.pop(0).get(website)
            check("Website", f"{website} loads (HTTP {status})" if status and status > 0 else f"{website} did not load", website)
        if results:
            kind, _ = results[0]
            check("Authorize URL", PROBE_TEXT[kind], facts["authorize_url"])

    ctx = Ctx(name=name, slug=slug, facts=facts or {}, checks=checks, emit=emit, upload=lambda u: upload_shot(u, slug))
    ctx.seen.update({u: "" for u in [website, (facts or {}).get("authorize_url", ""), "https://docs.composio.dev/toolkits/managed-auth"] if u})
    agent = build(ctx)
    run_id = uuid.uuid4()
    config = {"run_id": run_id, "run_name": f"guide: {name}", "recursion_limit": 120}
    task_names: dict[str, str] = {}
    observed: list[Claim] = []
    draft: GuideDraft | None = None
    line("Orchestrator is briefing three specialists", who="orchestrator")
    try:
        async with asyncio.timeout(540):  # well inside Cloud Run's 900s request timeout
            async for ns, update in agent.astream({"messages": [{"role": "user", "content": f"Write the OAuth app setup guide for {name}."}]},
                                                  config=config, stream_mode="updates", subgraphs=True):
                if ns:  # inside a specialist; tools report their own progress
                    continue
                for val in (update or {}).values():
                    if not isinstance(val, dict):
                        continue
                    if val.get("structured_response") is not None:
                        draft = val["structured_response"]
                    for m in val.get("messages") or []:
                        for tc in getattr(m, "tool_calls", None) or []:
                            if tc["name"] == "task":
                                who = tc["args"].get("subagent_type", "specialist").replace("-", " ")
                                task_names[tc["id"]] = who
                                line(f"→ {who}: {tc['args'].get('description', '')[:160]}", who="orchestrator")
                            elif tc["name"] == "GuideDraft":
                                line("Assembling the guide", who="orchestrator")
                        if getattr(m, "type", "") == "tool" and getattr(m, "name", "") == "task":
                            who = task_names.get(m.tool_call_id, "specialist")
                            data = report_findings(who, str(m.content), line)
                            if who == "portal explorer":
                                for o in data.get("observations", []):
                                    try:
                                        observed.append(Claim.model_validate(o))
                                    except ValueError:  # skip a malformed observation rather than lose the run
                                        pass
    except TimeoutError:
        line("Research ran out of time", "uncertain", who="orchestrator")
    if draft is None:
        emit({"kind": "error", "text": "The agents did not produce a guide. Try again in a minute."})
        return None
    if isinstance(draft, dict):
        draft = GuideDraft.model_validate(draft)

    guide = Guide(**draft.model_dump(), slug=slug, app_name=name, website=website, logo=(facts or {}).get("logo", ""),
                  composio=facts or {}, checks=checks, observed=observed, model=ORCH_MODEL,
                  screenshots=[Entry(label=page, value=ours) for ours, page in ctx.shots.items()])
    downgraded = gate(guide, ctx.seen, ctx.browsed, ctx.shots, (facts or {}).get("redirect_uri", ""))
    line(f"Evidence check: {downgraded} claim(s) downgraded (source not read this run, or a number not on the cited page)", who="checks")
    async with httpx.AsyncClient() as client:
        urls = {c.source_url for c in claims(guide) if c.source_url} | {s.url for s in guide.steps if s.url}
        guide.links = await check_links(urls, client)
    broken = apply_links(guide, guide.links)
    line(f"Link check: {len(guide.links)} links, {sum(v in (0, 404, 410) for v in guide.links.values())} broken"
         + (f", {broken} claim(s) marked uncertain" if broken else ""), who="checks")
    now = datetime.now(timezone.utc)
    for c in [*claims(guide), *checks]:
        c.checked_at = f"{now:%Y-%m-%d}" if c.source_url else ""
    guide.generated_at = now.isoformat(timespec="seconds")
    guide.seconds = int(time.time() - t0)
    guide.trace_url = await asyncio.to_thread(share_trace, run_id)
    await asyncio.to_thread(save_guide, guide)
    emit({"kind": "done", "text": f"Guide ready in {guide.seconds}s", "url": f"/guides/{slug}"})
    return guide


def report_findings(who: str, content: str, line):
    """Turn a specialist's JSON result into one progress line per claim."""
    try:
        data = json.loads(content[content.find("{"):content.rfind("}") + 1])
    except ValueError:
        line(f"{who} finished (no structured result)", "uncertain", who=who)
        return {}
    items = []
    for v in data.values():
        if isinstance(v, dict) and "tag" in v:
            items.append(v)
        elif isinstance(v, list):
            items += [x for x in v if isinstance(x, dict) and "tag" in x]
    for x in items:
        text = (f"{x['label']}: " if x.get("label") else "") + x.get("text", "")[:140]
        line(text, x.get("tag", ""), x.get("source_url", ""), who=who)
    line(f"{who} finished: {len(items)} findings", who=who)
    return data


if __name__ == "__main__":
    def emit(e):
        tag = f"[{e.get('tag')}] " if e.get("tag") else ""
        print(f"{e.get('who', e['kind']):>20} | {tag}{e.get('text', '')} {e.get('url', '')}", flush=True)
    g = asyncio.run(run(" ".join(sys.argv[1:]), emit))
    if g:
        os.makedirs("out", exist_ok=True)
        with open(f"out/{g.slug}.{g.model}.json", "w") as f:  # local copy for comparisons and verification
            f.write(g.model_dump_json(indent=1))
        print(f"saved guides/{g.slug}.json  difficulty={g.difficulty}  trace={g.trace_url}")
