# OAuth App Guides

**Live:** https://oauth-guides-968299856642.us-central1.run.app

Look up an app and see what it takes to get an OAuth app ready for your users:
- how hard it is;
- how long it takes, split into "working for you in development" and "ready for any customer" (including review waits);
- what can block approval;
- which steps an agent can do and where you take over.

Type any app name or website and a team of agents researches it live, in 3–5 minutes, then produces a guide you can share. Every claim is tagged **tested**, **sourced** or **uncertain**, and code enforces those tags.

The library has Linear (easy), Slack (medium), HubSpot (medium), Pipedrive (medium) and Gmail (hard).

## The problem, as I understood it

There are three layers:
1. **The provider OAuth app.** The developer registers it in the provider's console. It holds the client ID and secret, redirect URI, scopes and the consent-screen branding, and sometimes needs review.
2. **The integration.** A Composio auth config that holds those credentials.
3. **The user connections.** Each end user consents on that app's screen.

Composio provides managed OAuth apps for 124 toolkits. For the other 258, the developer must bring their own app before a single user can connect. Even with a managed app, developers often need their own before launch: their name on the consent screen, extra scopes, their own quota.

The hard part isn't the form. It's the unknowns: how long review takes, what triggers it, and what breaks after launch.

## Scope, and why

- **"Ready for any customer" is the finish line.** Difficulty and time measure the whole path, not just creating the app. That's where developers get surprised: Gmail's security assessment, HubSpot's 25-install cap, Slack's limits on unlisted apps.
- **Composio's facts are ground truth.** Each guide opens with "Do you need your own app?", answered from Composio's API. It ends with the hand-off: paste the credentials into a Composio auth config, then test the connection. The redirect URI and default scopes come from Composio, never from the model.
- **Agents work on public pages only.** They read docs, search, and open the developer portal in a real browser for screenshots, but never sign up, log in or submit forms. That's the line between what an agent can safely try and where the human takes over. It's also why setup steps are *sourced*, never *tested*.
- **Honesty is built in, not prompted.** Model output is checked by code (see VERIFICATION.md). Uncertain claims are listed at the end of each guide, and every guide links to its public research trace.

## How it works

```
app name or URL
  → resolve to a Composio toolkit (plain code: Composio API)
  → plain checks: website loads, authorize URL probed with a fake client_id
  → lead writer (Deep Agents) briefs 3 specialists that run in parallel:
       setup researcher     – docs: app type, steps, field values, prerequisites
       approval researcher  – review rules, waits, costs, limits on unreviewed apps
       portal explorer      – real browser on public pages, screenshots
  → lead writer assembles the guide (structured output)
  → evidence gate + link check (plain code) → saved to Cloud Storage → /guides/<app>
```

- **Tools:** all external research goes through a Composio session using `composio_search` (web search, page reading) and `browser_tool` (browsing, screenshots).
- **Model:** Gemini `gemini-3.8-flash` for every agent. It beat `gemini-3.1-pro-preview` on Gmail: faster, cheaper, more detailed.
- **Tracing:** every run is traced in LangSmith, and each guide links to its public trace.
- **App:** FastAPI with server-rendered pages. Live progress streams over SSE and replays if you reconnect. Each guide also has a Markdown version at `/guides/<app>.md`.

## Key decisions

- **Deep Agents with three parallel specialists.** The research questions are independent. Parallel runs keep each specialist's context small and cut wall time. The lead writer must dispatch all three in its first turn.
- **Code-enforced evidence rules.** This came out of verification:
  - "tested" needs a page the browser actually opened;
  - "sourced" needs a page actually read this run, and every number must appear on it;
  - dead links downgrade their claims.
- **Corrections go into the pipeline, not the guides.** When the audit found errors we changed prompts or rules and regenerated; we never hand-edited.
- **One instance, one live run at a time, 30 new guides a day.** That's the simplest abuse and cost control (about $0.12–0.16 of Gemini usage per guide). Existing guides are served instead of regenerated.

## Verification

See [VERIFICATION.md](VERIFICATION.md).

I audited all 109 sourced claims in three guides by hand: 42% confirmed, 30% partly supported, 26% not on the cited page, 2% wrong. Each systematic error became a pipeline fix.

Review also caught Slack's 2025 rate limits for non-Marketplace apps, which the first Slack guide missed.

## Limitations

- **"Sourced" means a page was read and cited, not that a second reader checked it supports the claim.** The audit shows that gap, and two known residual errors remain. Next step: a citation-checking pass that re-reads each cited page.
- **Nothing behind a login is tested.** No app was submitted for review, so wait times are the providers' own estimates.
- **Third-party sources for numbers are discouraged in prompts but not blocked in code.** Telling an official domain from a third-party one isn't reliable from the URL alone (Slack's docs moved to docs.slack.dev).
- **Runs live in memory on a single Cloud Run instance.** A redeploy during a run loses that run, but saved guides are unaffected.
- **5 guides, not 100.** Scaling means looping the CLI over Composio's OAuth toolkits, at about 4 minutes and $0.15 per guide. The home page lists every guide from storage on each view; at 100 guides that should become an index file.

## Run it

You'll need Python 3.13, [uv](https://docs.astral.sh/uv/), a Google Cloud Storage bucket, and Application Default Credentials (`gcloud auth application-default login`).

```bash
uv sync
export GEMINI_API_KEY=... COMPOSIO_API_KEY=... BUCKET=your-bucket
export LANGSMITH_API_KEY=... LANGSMITH_TRACING=true LANGSMITH_PROJECT=oauth-guides   # optional; enables trace links
uv run python -m app.check                     # self-check of the evidence rules
uv run python -m app.pipeline "Linear"         # generate one guide from the CLI
uv run uvicorn app.main:app --port 8080        # web app
```

Deploy (Cloud Run, keys in Secret Manager):

```bash
gcloud run deploy oauth-guides --source . --region us-central1 --allow-unauthenticated \
  --timeout 900 --max-instances 1 --no-cpu-throttling --memory 1Gi --service-account <sa> \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest,COMPOSIO_API_KEY=composio-api-key:latest,LANGSMITH_API_KEY=langsmith-api-key:latest \
  --set-env-vars BUCKET=<bucket>,LANGSMITH_TRACING=true,LANGSMITH_PROJECT=oauth-guides,DAILY_CAP=30
```

The bucket needs public read for screenshots, and the service account needs object access to the bucket and secret access to the three secrets.

## Repo

```
app/guide.py      data model, evidence gate, probes, Composio toolkit lookup
app/agent.py      the agent team and its Composio tools
app/pipeline.py   one guide run, storage, CLI
app/main.py       web routes, live streaming, limits
app/check.py      self-check
app/templates/    pages and the Markdown version
verification/     audited guides, source passages, audit script
```
