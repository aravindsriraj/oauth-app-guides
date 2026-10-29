"""The research team: a lead writer (the orchestrator) and three specialists, all researching through Composio tools."""
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx
from composio import Composio, SESSION_PRESET_DIRECT_TOOLS
from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import GoogleInvalidRequestError

from app.guide import BROKEN, ApprovalFindings, Claim, GuideDraft, PortalFindings, SetupFindings, check_links

MODEL = os.environ.get("MODEL", "gemini-3.8-flash")  # won a Flash-vs-Pro comparison on Gmail (VERIFICATION.md)
PAGE_CHARS = 12000  # how much of a page an agent sees, and what the evidence gate checks against
# Gemini occasionally rejects a valid call with 400 INVALID_ARGUMENT; the same call then succeeds. Retry it,
# and if it keeps failing let that agent continue with an error note instead of killing the whole run.
RETRY = ModelRetryMiddleware(max_retries=2, retry_on=(GoogleInvalidRequestError,), on_failure="continue")


@dataclass
class Ctx:
    """Per-run state shared by the tools and the pipeline."""
    name: str
    facts: dict
    checks: list[Claim]
    emit: Callable[[dict], None]
    upload: Callable[[str], str]          # remote screenshot url -> our public url
    seen: dict[str, str] = field(default_factory=dict)  # url read or returned by search -> text seen there
    browsed: set[str] = field(default_factory=set)   # urls the browser actually opened
    shots: dict[str, str] = field(default_factory=dict)  # our screenshot url -> page it shows


def _session():
    """A Composio session with exactly the tools the agents use (direct-tools preset: no tool search or auth prompts)."""
    return Composio().sessions.create(
        user_id="oauth-guide-bot", session_preset=SESSION_PRESET_DIRECT_TOOLS,
        toolkits=["composio_search", "browser_tool"],
        tools={"composio_search": ["COMPOSIO_SEARCH_WEB", "COMPOSIO_SEARCH_FETCH_URL_CONTENT"],
               "browser_tool": ["BROWSER_TOOL_CREATE_TASK", "BROWSER_TOOL_WATCH_TASK", "BROWSER_TOOL_STOP_TASK"]})


def _tools(ctx: Ctx, session, who: str):
    """Tools are built per specialist so every progress line says who is working."""
    say = lambda text, url="": ctx.emit({"kind": "line", "who": who, "text": text, "url": url})

    async def call_tool(slug, args):
        """Run one Composio tool through the session (the SDK is synchronous, so off the event loop)."""
        result = await asyncio.to_thread(session.execute, slug, arguments=args)
        if result.error:
            raise RuntimeError(str(result.error)[:300])
        return result.data or {}

    @tool
    async def web_search(query: str) -> str:
        """Search the web. Returns result URLs with snippets. The 'summary' is a lead, never a citable source."""
        say(f"searching “{query}”")
        try:
            found = await call_tool("COMPOSIO_SEARCH_WEB", {"query": query})
        except Exception as e:
            return f"search failed: {e}"
        results = [{"url": c.get("url", ""), "title": c.get("title", ""), "snippet": (c.get("snippet") or "")[:300]}
                   for c in found.get("citations") or []]
        for r in results:  # a cited result counts as seen, with its snippet as the text the agent saw
            if r["url"]:
                ctx.seen[r["url"]] = ctx.seen.get(r["url"], "") + " " + r["snippet"]
        return json.dumps({"summary": (found.get("answer") or "")[:1500], "results": results})

    @tool
    async def read_page(url: str) -> str:
        """Read the text of one web page (docs, help center, policy page)."""
        say("reading", url)
        async with httpx.AsyncClient() as http:
            status = (await check_links({url}, http)).get(url)
        if status in BROKEN:  # the fetch tool can serve a cached copy of a page that no longer exists
            return f"{url} no longer exists (HTTP {status}). Find the current page instead."
        try:
            fetched = await call_tool("COMPOSIO_SEARCH_FETCH_URL_CONTENT", {"urls": [url]})
            text = (fetched.get("results") or [{}])[0].get("text") or ""
        except Exception as e:
            return f"could not read {url}: {e}"
        if not text.strip():
            return f"{url} returned no readable text"
        ctx.seen[url] = text[:PAGE_CHARS]
        return f"URL: {url}\n\n{text[:PAGE_CHARS]}"

    @tool
    async def browse(task: str, start_url: str) -> str:
        """Open a public page in a real browser and do a short look-around task, e.g. 'find the developer signup and
        report what it asks for'. Returns what was observed and the URLs visited."""
        say("opening browser at", start_url)
        safety = ("Rules: only look at public pages. Never sign up, log in, submit forms or enter any data. "
                  "Stop when you reach a login or signup form and report what it asks for. Task: ")
        try:
            created = await call_tool("BROWSER_TOOL_CREATE_TASK", {"task": safety + task, "startUrl": start_url})
            task_id, steps, deadline = created["watch_task_id"], [], time.monotonic() + 90
            # poll every 3s until the browser task ends, reporting each new step; stop it after 90s
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                progress = await call_tool("BROWSER_TOOL_WATCH_TASK", {"taskId": task_id})
                for step in (progress.get("steps") or [])[len(steps):]:
                    say(f"browser step {step.get('number', len(steps) + 1)}", step.get("url", ""))
                steps = progress.get("steps") or steps
                if str(progress.get("status", "")).lower() in ("finished", "failed", "stopped"):
                    break
            else:
                await call_tool("BROWSER_TOOL_STOP_TASK", {"taskId": task_id})
                progress["output"] = (progress.get("output") or "") + " (stopped after 90s)"
        except Exception as e:
            return f"browser failed: {e}"
        visited = sorted({step["url"] for step in steps if step.get("url")})
        ctx.browsed.update(visited)
        for step in [s for s in steps if s.get("screenshotUrl")][-2:]:  # keep the last two for the guide's gallery
            try:
                ctx.shots[await asyncio.to_thread(ctx.upload, step["screenshotUrl"])] = step.get("url", "")
            except Exception:
                pass  # a missing screenshot shouldn't fail the research
        return json.dumps({"success": progress.get("isSuccess"), "observed": progress.get("output") or "", "visited": visited})

    return web_search, read_page, browse


RULES = """
Evidence rules (strict):
- "sourced": source_url is the exact URL of a page you read with read_page, or a result URL from web_search whose snippet states the fact. The search summary is never a source.
- "tested": only what the browser itself experienced this run (a page loaded, a form asked for X, a button or plan gate was there), with source_url = a URL the browse tool listed in "visited". Policy text you merely read on a page is "sourced", not "tested".
- "uncertain": everything else: inferences, anything from memory, and any number (days, prices, user limits) without a source.
- Use the provider's own domains (docs, developer portal, help center) first. Use a third-party page only when official docs are silent, and never for numbers. Never invent or guess URLs.
- One fact per claim, plain words, no marketing language. Keep lists short: at most 8 steps and 6 items per other list, most important first."""


SETUP = """
You research how to create the OAuth app on {app}'s developer platform. From official docs find:
- which kind of app to create (e.g. public vs private, marketplace vs custom) and why;
- the developer console URL (portal_url);
- prerequisites: account type, separate developer account, paid plan or partner program, verified domain, privacy policy and terms URLs, logo, support email, demo video;
- ordered steps from signing in to the console until you have a client ID and secret (plus submitting for review if required). For each step: the direct page link, and in fields only the form fields the docs show for that page, with what to enter (redirect URL = the ground-truth redirect URI; scopes only if the form has a scopes field). If the provider takes scopes in the authorization request instead of the form, say so in a pitfall: Composio sends them. Do not include Composio steps;
- pitfalls (things that silently break setup) and where to get help.
A step's actor is "agent" only if it needs no login to {app} (drafting text, preparing values); otherwise "you"."""

APPROVAL = """
You research what stands between a working {app} OAuth app and one that any customer can install:
- scopes: every needed scope that triggers review or counts as sensitive/restricted, plus at most 6 others (review: none, review, restricted, unknown). Cite the page that lists the scope;
- is app review, verification or a marketplace listing required, and when (always, above a user count, only for listing, only for some scopes)? Use approval claims labelled: When required, What's checked, Wait, Cost, Common rejections, Before approval (limits such as test users, token expiry, install caps);
- install_cap and unreviewed_limits (always answer both): how many accounts can install before review or listing, and what lower API limits or terms restrictions apply to apps that are not reviewed or listed, even after launch. Read the provider's page on app distribution or install limits, and make one search "{app} API changelog install limits rate limits unlisted OR unreviewed OR non-marketplace apps";
- pitfalls specific to review."""

PORTAL = """
You look at {app}'s public developer pages in a real browser, as a developer starting out would. Use browse at most 2 times:
first on the developer portal or docs landing page, then on the developer signup or 'create app' entry point if one is linked.
Find out: whether developer signup is self-serve or 'contact sales'; whether it asks for a credit card or a paid plan; whether a separate developer account exists; where app creation starts.
Split what you report:
- observations: only what the browser itself experienced (the signup form asked for X, the console URL redirected to a login wall, a pricing gate appeared). Tag "tested", source_url = a URL from that browse call's "visited" list.
- stated: what the pages say (requirements, policies, docs text). Tag "sourced", never "tested"."""

LEAD = """
You are the lead researcher. You write a setup guide a developer can follow and share without help.

Process:
1. Your FIRST reply must call the `task` tool three times in that same reply: subagent_type setup-researcher, approval-researcher and portal-explorer, each with a short brief naming {app} and its website. They run in parallel. Do not use write_todos, the file tools or general-purpose.
2. When all three have returned you may make at most one follow-up `task` call, only if the steps or the approval information are completely missing.
3. Then write the final guide.

Assembly rules:
- Copy tags and source_urls from the findings exactly. Never upgrade a tag. Only portal-explorer observations may be "tested", and never a step (no one logs in to the provider).
- If sources conflict, keep the official one and add an "uncertain" pitfall describing the conflict.
- If a specialist failed or found nothing, write its parts with tag "uncertain" and say what is unknown.
- difficulty rates the path until any customer can connect. easy = only a normal account, no review anywhere on that path. medium = any one of: a separate developer or partner account, review for any needed scope, review or listing needed for broad distribution, install or user caps or reduced limits until reviewed. hard = a mandatory security assessment, a paid program, or weeks of required review. difficulty_why: one or two sentences.
- approval: include the approval researcher's install_cap and unreviewed_limits, labelled "Install cap before review" and "Limits until reviewed".
- dev_time: time until the developer has a working app for their own testing. launch_time: time until any customer can connect, including review waits; if installs are capped or API limits are reduced until review, launch_time includes that review. Put a short range in each label (e.g. "15-30 min", "Same day", "4-8 weeks") and the explanation in text; unsourced numbers are "uncertain".
- flags: 0-4 short warnings (e.g. "Security assessment", "Developer account", "Marketplace review", "Paid plan").
- own_app_reasons: only reasons that apply to {app}: their own name on the consent screen (say so if {app} shows it only after verification), scopes beyond Composio's defaults, faster triggers, custom or regional instances. Own rate limits count only if the source says {app}'s limits are counted per app, client or project, and the claim must name that unit as the source states it; if limits are per user, token or customer account, leave this reason out. Downsides of owning an app (e.g. stricter limits until it is reviewed or listed) are pitfalls, not reasons.
- steps: from the setup findings. Use the ground-truth redirect URI. Do not add Composio steps; the page adds them.
- email_subject / email_body: a ready-to-send email to {app}'s developer support asking about OAuth app requirements, review and timelines for this use case, with [placeholders] for the sender's details.
- If {app} does not offer OAuth for third-party apps, say so in difficulty_why and app_type and describe the alternative."""

BUDGET = "\nBudget: at most 3 web_search and 4 read_page calls, then give your answer."


def build(ctx: Ctx):
    """The lead writer and its three specialists. Every prompt starts with the app's ground truth and ends with RULES."""
    session = _session()
    model = ChatGoogleGenerativeAI(model=MODEL, max_retries=3)
    c = ctx.facts
    ground = json.dumps({k: c.get(k) for k in ("toolkit", "auth_modes", "managed", "redirect_uri", "default_scopes",
                                               "authorize_url", "token_url", "app_url")} if c else {"composio_toolkit": None}, indent=1)
    checks = "\n".join(f"- {x.label}: {x.text}" for x in ctx.checks)
    about = (f"App: {ctx.name}. A developer is building an AI agent product and wants its users to connect their "
             f"{ctx.name} accounts through Composio, which needs an OAuth app registered with {ctx.name}.\n"
             f"Ground truth from Composio's API (use the redirect URI and scopes exactly):\n{ground}\n"
             f"Our own HTTP checks:\n{checks}\n")
    prompt = lambda body: about + body.format(app=ctx.name) + RULES
    s_search, s_read, _ = _tools(ctx, session, "setup researcher")
    a_search, a_read, _ = _tools(ctx, session, "approval researcher")
    _, _, p_browse = _tools(ctx, session, "portal explorer")
    subagents = [
        {"name": "setup-researcher", "model": model,
         "description": "Finds from official docs how to create an OAuth app on {app}: app type, console URL, ordered steps with field values, prerequisites, pitfalls, support.".format(app=ctx.name),
         "system_prompt": prompt(SETUP) + BUDGET, "tools": [s_search, s_read],
         "response_format": ToolStrategy(SetupFindings), "middleware": [RETRY, ModelCallLimitMiddleware(run_limit=12)]},
        {"name": "approval-researcher", "model": model,
         "description": "Finds what it takes for a {app} OAuth app to be usable by any customer: which scopes trigger review, review process, waits, costs, rejection reasons, limits before approval.".format(app=ctx.name),
         "system_prompt": prompt(APPROVAL) + BUDGET, "tools": [a_search, a_read],
         "response_format": ToolStrategy(ApprovalFindings), "middleware": [RETRY, ModelCallLimitMiddleware(run_limit=12)]},
        {"name": "portal-explorer", "model": model,
         "description": "Uses a real browser on {app}'s public developer pages: is signup self-serve, is a credit card or paid plan needed, where app creation starts; takes screenshots.".format(app=ctx.name),
         "system_prompt": prompt(PORTAL), "tools": [p_browse],
         "response_format": ToolStrategy(PortalFindings),  # each browse can take 90s, so cap them
         "middleware": [RETRY, ModelCallLimitMiddleware(run_limit=6), ToolCallLimitMiddleware(tool_name="browse", run_limit=3)]},
    ]
    return create_deep_agent(model=model, system_prompt=prompt(LEAD), subagents=subagents,
                             response_format=ToolStrategy(GuideDraft),
                             middleware=[RETRY, ModelCallLimitMiddleware(run_limit=8)], name="oauth-guide")
