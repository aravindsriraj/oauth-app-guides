# Code review: simplicity and readability

A structured pass over the whole codebase, done after the guides were audited. Numbers are from `radon` (cyclomatic complexity per function; maintainability per file) and `pyflakes`.

## Method

Every function was read against five questions:
1. Does it need to exist?
2. Is it named for what it does?
3. Is its behavior obvious, or does it have a docstring?
4. Do its variable names say what they hold?
5. Is it short enough to follow in one read?

Each component was then challenged with run data: was it ever exercised, and would something simpler do?

## Modules

| File | Lines | Job | Functions by complexity | Maintainability |
|---|---|---|---|---|
| `app/guide.py` | 319 | Guide data model; Composio toolkit lookup; probes; evidence rules | 14 A, 4 B, 2 C (`toolkit_facts`, `downgrade`) | A |
| `app/pipeline.py` | 234 | One guide run in three steps (plain checks, research, evidence), plus storage and CLI | 10 A, 4 B, 2 C (`research`, `report_findings`) | A |
| `app/agent.py` | 214 | Composio-backed tools; the four prompts as named constants; `build()` wiring | all A | A |
| `app/main.py` | 124 | Routes, live progress over SSE, one-run-at-a-time and daily limits | 6 A, 1 C (`generate`, mostly input checks) | A |
| `app/check.py` | 97 | Assertions for every evidence rule and the probe classifier | A | A |

Overall: average A (3.8), nothing worse than C, pyflakes clean.

## Components challenged

| Component | Simpler alternative considered | Outcome |
|---|---|---|
| Authorize-URL probe | Drop it; let the agents describe the endpoint | **Kept and fixed.** It's the only tested evidence that needs no LLM. Usage data showed its `login_wall` outcome fired only on Google's error page, which is *wrong*: the probe sent no scope and read only the first 20k characters. The category is now deleted, the probe sends the toolkit's default scopes and reads the whole page, and Gmail correctly reports "rejects an unregistered client_id". The Gmail guide was regenerated and re-audited. |
| Evidence gate (8 rules) | Trust the model's tags | **Kept.** Each rule exists because the audit found the error it prevents (VERIFICATION.md). It was split from one D (27) function into one small function per rule. |
| Per-step screenshots | Keep attaching screenshots to steps | **Cut.** Agents never log in, so a public-page screenshot matched a step 0–1 times per guide. Screenshots now appear only in the "What our browser saw" gallery. |
| Daily cap stored in Cloud Storage | An in-memory counter | **Kept.** Cloud Run scales to zero, so an in-memory count resets and caps nothing. The file version is 7 lines. |
| SSE with replay | A page that polls a status URL | **Kept.** Replay via `Last-Event-ID` is a few lines, and closing the tab mid-run doesn't lose the feed. |
| Three specialists in parallel | One agent doing everything in sequence | **Kept.** The lead writer dispatches all three in one turn; runs take 2.5–5 minutes rather than adding up sequential research. |
| LangChain provider package from Composio | Composio's generated LangChain tools | **Cut (dependency removed).** Three small tools wrapping `session.execute` give exact control over what counts as "seen". |
| Two model settings | `ORCH_MODEL` and `SUB_MODEL` | **Cut to one `MODEL`.** Flash won for every role. |
| Retrying failed model calls (added during the review) | Rely on the client's `max_retries` | **Added: one line using LangChain's `ModelRetryMiddleware`.** A production run died on Gemini's intermittent `400 INVALID_ARGUMENT`. The identical input succeeded a minute later, and the client doesn't retry 400s. Failed runs are now also logged with a traceback. |

## Removed during the review

- Per-step screenshots: a data field, a gate rule, two prompt lines, two template blocks, and the browse tool's output field.
- Per-claim `checked_at`, never displayed.
- `managed_scopes` in the toolkit facts, never read.
- The probe's unused second return value.
- The `login_wall` probe outcome, which was wrong whenever it fired.
- A `resolved` flag on `run()`, now a plain argument.
- An unused `Ctx.slug` field.
- An unused template global.
- A stray verification file.

## Readability fixes

- **Docstrings** wherever behavior isn't obvious from the name: `query_for`, `verdict`, `classify_probe`, `norm`, `claims`, `numbers_on_page`, `apply_links`, `bucket`, `share_trace`, `call_tool`.
- **Descriptive names** instead of single letters that meant different things in different places: `c` and `d` became `facts` / `toolkit` / `guide`, and `w`, `tid`, `m`, `tc`, `v`, `x` became `progress`, `task_id`, `message`, `call`, `value`, `item`. `q` became `query`.
- **Comments** on the stream loop (what each update holds), the browser polling loop, and why `BROKEN` excludes 403/429.
- **Long functions split into named steps:** `run()` (C 18 → B 8) and `gate()` (D 27 → B 6).
- **Prompts** moved to named constants, verified byte-identical to the inline versions.

## How each change was checked

- **Self-check:** `uv run python -m app.check`.
- **Rule test** on all shipped guides: `verification/regate.py`.
- **Prompt identity** before and after each refactor.
- **Identical `toolkit_facts` output** for six real toolkits.
- **Every route rendered** against the stored guides.
- **A full live run** after each change to the tools or pipeline, deleted afterwards.
