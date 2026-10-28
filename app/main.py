"""Web app: library, live generation over SSE, and shareable guide pages."""
import asyncio
import json
import os
import re
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app import pipeline
from app.guide import claims, resolve, slugify, verdict

app = FastAPI()
T = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
T.env.filters["domain"] = lambda u: urlparse(u).netloc.removeprefix("www.")
T.env.globals.update(verdict=verdict, claims=claims, audited=AUDITED)
DAILY_CAP = int(os.environ.get("DAILY_CAP", "30"))
RUNS: dict[str, dict] = {}   # slug -> {"name", "events", "done", "task"}; one instance, so memory is the truth
ACTIVE: str | None = None    # slug of the one live run allowed at a time
ORDER = {"easy": 0, "medium": 1, "hard": 2}
AUDITED = {"linear", "slack", "hubspot", "gmail", "pipedrive"}  # checked claim by claim, see VERIFICATION.md


def page(request, template, status=200, **ctx):
    return T.TemplateResponse(request, template, ctx, status_code=status)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    guides = sorted(pipeline.list_guides(), key=lambda g: (ORDER[g.difficulty], g.app_name.lower()))
    return page(request, "home.html", guides=guides, active=ACTIVE and RUNS.get(ACTIVE, {}).get("name"), active_slug=ACTIVE)


@app.post("/generate")
async def generate(request: Request, app_input: str = Form(alias="app")):
    global ACTIVE
    q = app_input.strip()
    if not q or len(q) > 80 or not re.fullmatch(r"[\w .:/&+\-]+", q):
        return page(request, "message.html", 400, title="Try another name",
                    text="Enter an app name or website, up to 80 characters.")
    async with httpx.AsyncClient(timeout=15) as client:
        facts = await resolve(q, client)
    name = facts["name"] if facts else q
    slug = slugify(facts["toolkit"] if facts else name)
    if not slug:
        return page(request, "message.html", 400, title="Try another name", text="That name has no letters or digits.")
    if slug in RUNS and not RUNS[slug]["done"]:
        return RedirectResponse(f"/live/{slug}", 303)
    if await asyncio.to_thread(pipeline.load_guide, slug):
        return RedirectResponse(f"/guides/{slug}", 303)
    if ACTIVE:
        return page(request, "message.html", 429, title="One guide at a time",
                    text=f"The agents are researching {RUNS[ACTIVE]['name']} right now.", link=f"/live/{ACTIVE}", link_text="Watch it live")
    if not await asyncio.to_thread(pipeline.take_daily_slot, DAILY_CAP):
        return page(request, "message.html", 429, title="Daily limit reached",
                    text=f"This demo generates up to {DAILY_CAP} new guides a day. The library is still open.")
    ACTIVE = slug
    run = RUNS[slug] = {"name": name, "events": [], "done": False}

    async def go():
        global ACTIVE
        try:
            await pipeline.run(q, run["events"].append, facts)
        except Exception as e:  # the page must always learn the run ended
            run["events"].append({"kind": "error", "text": f"The run failed ({type(e).__name__}). Try again in a minute."})
        finally:
            run["done"], ACTIVE = True, None

    run["task"] = asyncio.create_task(go())  # not tied to the request: closing the tab never stops a run
    return RedirectResponse(f"/live/{slug}", 303)


@app.get("/live/{slug}", response_class=HTMLResponse)
def live(request: Request, slug: str):
    if slug in RUNS:
        return page(request, "live.html", slug=slug, name=RUNS[slug]["name"])
    if pipeline.load_guide(slug):
        return RedirectResponse(f"/guides/{slug}", 303)
    return page(request, "message.html", 404, title="No run here",
                text="This run isn't active (the server may have restarted). Start it again from the home page.")


@app.get("/live/{slug}/events")
async def events(request: Request, slug: str):
    run = RUNS.get(slug)
    if not run:
        return PlainTextResponse("", 404)
    start = int(request.headers.get("last-event-id", "-1")) + 1  # EventSource resends this on reconnect

    async def stream():
        i, idle = start, 0.0
        while True:
            while i < len(run["events"]):
                yield f"id: {i}\ndata: {json.dumps(run['events'][i])}\n\n"
                i, idle = i + 1, 0.0
            if run["done"]:
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.5)
            idle += 0.5
            if idle >= 15:
                yield ": ping\n\n"
                idle = 0.0
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/guides/{name}")
def guide(request: Request, name: str):
    md = name.endswith(".md")
    g = pipeline.load_guide(slugify(name.removesuffix(".md")))
    if not g:
        return page(request, "message.html", 404, title="Guide not found", text="Generate it from the home page.")
    if md:
        return PlainTextResponse(T.get_template("guide.md").render(g=g), media_type="text/markdown; charset=utf-8")
    return page(request, "guide.html", g=g)


@app.get("/health")  # Cloud Run reserves paths ending in "z"
def healthz():
    return PlainTextResponse("ok")
