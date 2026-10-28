"""The research team: an orchestrator plus three specialists, all using Composio tools."""
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from composio import Composio, SESSION_PRESET_DIRECT_TOOLS
from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from app.guide import ApprovalFindings, Claim, GuideDraft, PortalFindings, SetupFindings

ORCH_MODEL = os.environ.get("ORCH_MODEL", "gemini-3.8-flash")
SUB_MODEL = os.environ.get("SUB_MODEL", "gemini-3.8-flash")
PAGE_CHARS = 12000
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


@dataclass
class Ctx:
    """Per-run state shared by the tools and the pipeline."""
    name: str
    slug: str
    facts: dict
    checks: list[Claim]
    emit: Callable[[dict], None]
    upload: Callable[[str], str]          # remote screenshot url -> our public url
    seen: set[str] = field(default_factory=set)      # urls that appeared in tool results
    browsed: set[str] = field(default_factory=set)   # urls the browser actually opened
    shots: dict[str, str] = field(default_factory=dict)  # our screenshot url -> page it shows


def _session():
    return Composio().sessions.create(
        user_id="oauth-guide-bot", session_preset=SESSION_PRESET_DIRECT_TOOLS,
        toolkits=["composio_search", "browser_tool"],
        tools={"composio_search": ["COMPOSIO_SEARCH_WEB", "COMPOSIO_SEARCH_FETCH_URL_CONTENT"],
               "browser_tool": ["BROWSER_TOOL_CREATE_TASK", "BROWSER_TOOL_WATCH_TASK", "BROWSER_TOOL_STOP_TASK"]})


def _tools(ctx: Ctx, session, who: str):
    """Tools are built per specialist so every progress line says who is working."""
    say = lambda text, url="": ctx.emit({"kind": "line", "who": who, "text": text, "url": url})

    async def run(slug, args):
        r = await asyncio.to_thread(session.execute, slug, arguments=args)
        if r.error:
            raise RuntimeError(str(r.error)[:300])
        return r.data or {}

    @tool
    async def web_search(query: str) -> str:
        """Search the web. Returns result URLs with snippets. The 'summary' is a lead, never a citable source."""
        say(f"searching “{query}”")
        try:
            d = await run("COMPOSIO_SEARCH_WEB", {"query": query})
        except Exception as e:
            return f"search failed: {e}"
        results = [{"url": c.get("url", ""), "title": c.get("title", ""), "snippet": (c.get("snippet") or "")[:300]}
                   for c in d.get("citations") or []]
        ctx.seen.update(r["url"] for r in results if r["url"])
        return json.dumps({"summary": (d.get("answer") or "")[:1500], "results": results})

    @tool
    async def read_page(url: str) -> str:
        """Read the text of one web page (docs, help center, policy page)."""
        say("reading", url)
        try:
            d = await run("COMPOSIO_SEARCH_FETCH_URL_CONTENT", {"urls": [url]})
            text = (d.get("results") or [{}])[0].get("text") or ""
        except Exception as e:
            return f"could not read {url}: {e}"
        if not text.strip():
            return f"{url} returned no readable text"
        ctx.seen.add(url)
        ctx.seen.update(URL_RE.findall(text[:PAGE_CHARS]))
        return f"URL: {url}\n\n{text[:PAGE_CHARS]}"

    @tool
    async def browse(task: str, start_url: str) -> str:
        """Open a public page in a real browser and do a short look-around task, e.g. 'find the developer signup and
        report what it asks for'. Returns what was observed, the URLs visited and screenshot URLs."""
        say("opening browser at", start_url)
        safety = ("Rules: only look at public pages. Never sign up, log in, submit forms or enter any data. "
                  "Stop when you reach a login or signup form and report what it asks for. Task: ")
        try:
            d = await run("BROWSER_TOOL_CREATE_TASK", {"task": safety + task, "startUrl": start_url})
            tid, steps, deadline = d["watch_task_id"], [], time.monotonic() + 90
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                w = await run("BROWSER_TOOL_WATCH_TASK", {"taskId": tid})
                for s in (w.get("steps") or [])[len(steps):]:
                    say(f"browser step {s.get('number', len(steps) + 1)}", s.get("url", ""))
                steps = w.get("steps") or steps
                if str(w.get("status", "")).lower() in ("finished", "failed", "stopped"):
                    break
            else:
                await run("BROWSER_TOOL_STOP_TASK", {"taskId": tid})
                w["output"] = (w.get("output") or "") + " (stopped after 90s)"
        except Exception as e:
            return f"browser failed: {e}"
        visited = sorted({s["url"] for s in steps if s.get("url")})
        ctx.browsed.update(visited)
        shots = []
        for s in [s for s in steps if s.get("screenshotUrl")][-2:]:
            try:
                ours = await asyncio.to_thread(ctx.upload, s["screenshotUrl"])
                ctx.shots[ours] = s.get("url", "")
                shots.append({"url": ours, "page": s.get("url", "")})
            except Exception:
                pass
        return json.dumps({"success": w.get("isSuccess"), "observed": w.get("output") or "", "visited": visited,
                           "screenshots": shots})

    return web_search, read_page, browse


RULES = """
Evidence rules (strict):
- "sourced": source_url is the exact URL of a page you read with read_page, or a result URL from web_search whose snippet states the fact. The search summary is never a source.
- "tested": only what the browser itself experienced this run (a page loaded, a form asked for X, a button or plan gate was there), with source_url = a URL the browse tool listed in "visited". Policy text you merely read on a page is "sourced", not "tested".
- "uncertain": everything else: inferences, anything from memory, and any number (days, prices, user limits) without a source.
- Use the provider's own domains (docs, developer portal, help center) first. Use a third-party page only when official docs are silent, and never for numbers. Never invent or guess URLs.
- One fact per claim, plain words, no marketing language."""


def build(ctx: Ctx):
    session = _session()
    c = ctx.facts
    ground = json.dumps({k: c.get(k) for k in ("toolkit", "auth_modes", "managed", "redirect_uri", "default_scopes",
                                               "authorize_url", "token_url", "app_url")} if c else {"composio_toolkit": None}, indent=1)
    checks = "\n".join(f"- {x.label}: {x.text}" for x in ctx.checks)
    about = (f"App: {ctx.name}. A developer is building an AI agent product and wants its users to connect their "
             f"{ctx.name} accounts through Composio, which needs an OAuth app registered with {ctx.name}.\n"
             f"Ground truth from Composio's API (use the redirect URI and scopes exactly):\n{ground}\n"
             f"Our own HTTP checks:\n{checks}\n")
    s_search, s_read, _ = _tools(ctx, session, "setup researcher")
    a_search, a_read, _ = _tools(ctx, session, "approval researcher")
    _, _, p_browse = _tools(ctx, session, "portal explorer")
    sub_model = ChatGoogleGenerativeAI(model=SUB_MODEL, max_retries=3)
    budget = "\nBudget: at most 3 web_search and 4 read_page calls, then give your answer."
    subagents = [
        {"name": "setup-researcher",
         "description": f"Finds from official docs how to create an OAuth app on {ctx.name}: app type, console URL, ordered steps with field values, prerequisites, pitfalls, support.",
         "system_prompt": about + f"""
You research how to create the OAuth app on {ctx.name}'s developer platform. From official docs find:
- which kind of app to create (e.g. public vs private, marketplace vs custom) and why;
- the developer console URL (portal_url);
- prerequisites: account type, separate developer account, paid plan or partner program, verified domain, privacy policy and terms URLs, logo, support email, demo video;
- ordered steps from signing in to the console until you have a client ID and secret (plus submitting for review if required). For each step: the direct page link, and in fields what to enter (redirect URL = the ground-truth redirect URI; scopes = the ground-truth default scopes, translated to the provider's names if needed). Do not include Composio steps;
- pitfalls (things that silently break setup) and where to get help.
A step's actor is "agent" only if it needs no login to {ctx.name} (drafting text, preparing values); otherwise "you".""" + RULES + budget,
         "tools": [s_search, s_read], "model": sub_model,
         "response_format": ToolStrategy(SetupFindings), "middleware": [ModelCallLimitMiddleware(run_limit=12)]},
        {"name": "approval-researcher",
         "description": f"Finds what it takes for a {ctx.name} OAuth app to be usable by any customer: which scopes trigger review, review process, waits, costs, rejection reasons, limits before approval.",
         "system_prompt": about + f"""
You research what stands between a working {ctx.name} OAuth app and one that any customer can install:
- for each needed scope: does it trigger review or count as sensitive/restricted? (review: none, review, restricted, unknown);
- is app review, verification or a marketplace listing required, and when (always, above a user count, only for listing, only for some scopes)? Use approval claims labelled: When required, What's checked, Wait, Cost, Common rejections, Before approval (limits such as test users, token expiry, install caps);
- pitfalls specific to review.""" + RULES + budget,
         "tools": [a_search, a_read], "model": sub_model,
         "response_format": ToolStrategy(ApprovalFindings), "middleware": [ModelCallLimitMiddleware(run_limit=12)]},
        {"name": "portal-explorer",
         "description": f"Uses a real browser on {ctx.name}'s public developer pages: is signup self-serve, is a credit card or paid plan needed, where app creation starts; takes screenshots.",
         "system_prompt": about + f"""
You look at {ctx.name}'s public developer pages in a real browser, as a developer starting out would. Use browse at most 2 times:
first on the developer portal or docs landing page, then on the developer signup or 'create app' entry point if one is linked.
Report as observations: whether developer signup is self-serve or 'contact sales'; whether it asks for a credit card or a paid plan; whether a separate developer account exists; where app creation starts.
Tag observations "tested" with source_url set to a URL from that browse call's "visited" list. List the screenshot URLs exactly as browse returned them, with a short caption.""" + RULES,
         "tools": [p_browse], "model": sub_model,
         "response_format": ToolStrategy(PortalFindings),
         "middleware": [ModelCallLimitMiddleware(run_limit=6), ToolCallLimitMiddleware(tool_name="browse", run_limit=3)]},
    ]
    system = about + f"""
You are the lead researcher. You write a setup guide a developer can follow and share without help.

Process:
1. Your FIRST reply must call the `task` tool three times in that same reply: subagent_type setup-researcher, approval-researcher and portal-explorer, each with a short brief naming {ctx.name} and its website. They run in parallel. Do not use write_todos, the file tools or general-purpose.
2. When all three have returned you may make at most one follow-up `task` call, only if the steps or the approval information are completely missing.
3. Then write the final guide.

Assembly rules:
- Copy tags and source_urls from the findings exactly. Never upgrade a tag. Only portal-explorer observations may be "tested", and never a step (no one logs in to the provider).
- If sources conflict, keep the official one and add an "uncertain" pitfall describing the conflict.
- If a specialist failed or found nothing, write its parts with tag "uncertain" and say what is unknown.
- difficulty: easy = self-serve, no review for the needed scopes, under an hour. medium = separate developer account, review for some scopes, or a marketplace listing; days. hard = mandatory security review or assessment, weeks, or a cost. difficulty_why: one or two sentences.
- dev_time: time until the developer has a working app for their own testing. launch_time: time until any customer can connect, including review waits. Put a short range in each label (e.g. "15-30 min", "Same day", "4-8 weeks") and the explanation in text; unsourced numbers are "uncertain".
- flags: 0-4 short warnings (e.g. "Security assessment", "Developer account", "Marketplace review", "Paid plan").
- own_app_reasons: only reasons that apply to {ctx.name}: their own name on the consent screen, scopes beyond Composio's defaults, own rate limits, faster triggers, custom or regional instances.
- steps: from the setup findings. Attach a portal-explorer screenshot only to a step whose url is the page that screenshot shows. Use the ground-truth redirect URI. Do not add Composio steps; the page adds them.
- email_subject / email_body: a ready-to-send email to {ctx.name}'s developer support asking about OAuth app requirements, review and timelines for this use case, with [placeholders] for the sender's details.
- If {ctx.name} does not offer OAuth for third-party apps, say so in difficulty_why and app_type and describe the alternative.""" + RULES
    orch_model = ChatGoogleGenerativeAI(model=ORCH_MODEL, max_retries=3)
    return create_deep_agent(model=orch_model, system_prompt=system, subagents=subagents,
                             response_format=ToolStrategy(GuideDraft),
                             middleware=[ModelCallLimitMiddleware(run_limit=8)], name="oauth-guide")
