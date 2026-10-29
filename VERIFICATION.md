# Verification

What we tested, what sources support, what we corrected, and what we still can't verify.
All checks ran on 2026-10-28.

## 1. Checks that run on every guide

Every guide is checked by plain code, not by the model, before it is saved:

| Check | How | Tag it earns |
|---|---|---|
| Does Composio provide an OAuth app? | Composio toolkit API (`composio_managed_auth_schemes`) | tested |
| Does the website load? | HTTP request | tested |
| Is the provider's authorize URL live? | Request with a fake `client_id`, Composio's redirect URI and the toolkit's default scopes, then classify the whole response (rejects unknown app / renders / blocked / 404) | tested |
| What did the developer portal look like? | Browser agent on public pages only, with screenshots copied to our bucket | tested (observations only) |
| Was the cited page actually read? | A "sourced" claim keeps its tag only if its URL was read or returned by search during this run | otherwise uncertain |
| Is the number on the page? | Every number in a sourced claim must appear in the text the agent read at that URL | otherwise uncertain |
| Do the links work? | Every cited URL is fetched; a 404, 410 or DNS failure downgrades the claim | otherwise uncertain |
| Steps | A setup step is never "tested" (no one logs in); any "redirect" field is forced to Composio's redirect URI. Screenshots appear only under "What our browser saw" | n/a |

These rules live in `app/guide.py` (`gate`, `apply_links`, `classify_probe`) and are exercised by `uv run python -m app.check`.

## 2. Audit of the shipped library (all five guides, 143 sourced claims)

**Method.** The guides live on the site are saved as `verification/final-*.json`. For every claim tagged sourced, we fetched the cited page with the same Composio tool the agents use and pulled the best-matching passages (`uv run python verification/source_passages.py verification/final-linear.json`). We then read each claim against its page. Where a number, scope or rule mattered, we searched the full page for it. The passages aren't committed because they're copied documentation text; the script regenerates them.

| Guide | Sourced claims | Confirmed | Partly supported | Not on the cited page | Wrong |
|---|---|---|---|---|---|
| Linear | 26 | 13 | 10 | 3 | 0 |
| Slack | 28 | 11 | 14 | 3 | 0 |
| HubSpot | 30 | 22 | 6 | 2 | 0 |
| Gmail | 29 | 20 | 9 | 0 | 0 |
| Pipedrive | 30 | 22 | 5 | 3 | 0 |
| **Total** | **143** | **88 (62%)** | **44 (31%)** | **11 (8%)** | **0** |

- **No wrong claims remain.** The first pass of this audit found one: Linear's "Configure application details" step told you to "select the required scopes" in the app form. Linear's form has no scope field (its manifest docs list every field); scopes go in the authorization URL, which Composio builds. The cause was our setup prompt, which asked for a scopes value in every provider's form. It now asks only for fields the docs show. The regenerated Linear guide lists the real fields and adds a pitfall, "Scopes configured in authorize URL, not console". The Linear row above is that regenerated guide.
- **A wrong *tested* claim, found during the code review and fixed:** the Gmail guide said Google's authorize URL "sends visitors to a login page first". Our probe sent no scope, so Google showed an error page at a `/signin/...` path, and the classifier read the path as a login wall. It also scanned only the first 20k characters, missing Google's `invalid_client` text. The probe now sends default scopes and reads the whole page, the login-wall category is gone, and the regenerated guide (the Gmail row above) says correctly that Google rejects an unregistered client. The other four guides' probe results were re-run and are unchanged.
- **Third-party sources:** four Linear claims cite a third-party setup guide (docs.unified.to). Each matches Linear's own docs, for example default private distribution and the 2–80 character name rule. They're graded "partly supported" because the citation isn't Linear's.
- **Not on the cited page (12):** mostly plausible absences ("no install cap", "no fee") and consent-screen branding claims cited to a general OAuth page.
- **Partly supported (47):** the core fact is on the page but a detail isn't, such as a button label, "5 to 10 workspaces" where Slack says "5 or more", or a phrase like "CASA Tier 2".
- **Tested claims** (2–5 per guide) are browser observations of public pages: login walls, signup forms, "no credit card needed". The screenshots shown in each guide are the evidence. We did not re-run the browser for this audit.
- **Scopes:** 11 Gmail scopes, 5 Slack, 5 HubSpot, 2 Linear and 2 Pipedrive are shown as uncertain, because the scope-on-page rule found no page listing them. The scope names themselves come from Composio's defaults.

### Rule test on the shipped guides

`uv run python verification/regate.py verification/final-*.json` re-reads every cited page and re-runs the final evidence gate on each shipped guide. **All 206 claims across the five guides keep their tags.** As a control, the same script run on the pre-rules Pipedrive guide (`verification/audited-pipedrive.json`) flags 9 claims. Compared with that guide's first hand audit:
- **8 are real problems.** That's the wrong "own app = independent rate limits" reason (graded wrong by hand), six scope claims cited to an unrelated changelog post (graded not on page), and "Same day to 4 weeks", where the page says 21 business days (graded partly supported).
- **1 is a false positive.** "45 days … 6 months" is confirmed by hand, but the page writes "six months".
- **4 hand-flagged problems are missed.** They're citations that contain no number or scope (branding, admin access, cost, community link), which the rules can't check.
- **1 hand grade was wrong.** The "60-minute" token lifetime *is* on the cited page, and the rules correctly kept it.

One known false positive: the numbers rule looks for digits, so "6 months" fails against a page that writes "six months". This errs toward "uncertain", never toward overclaiming.

### First audit (three earlier guides, 109 sourced claims)

This audit drove the fixes below. It used the same method on the Linear, Pipedrive and Gmail guides from before those fixes, which are kept as `verification/audited-*.json`.

| Guide | Sourced claims | Confirmed | Partly supported | Not on the cited page | Wrong |
|---|---|---|---|---|---|
| Linear | 31 | 16 | 9 | 5 | 1 |
| Pipedrive | 35 | 15 | 8 | 11 | 1 |
| Gmail | 43 | 15 | 16 | 12 | 0 |
| **Total** | **109** | **46 (42%)** | **33 (30%)** | **28 (26%)** | **2 (2%)** |

- **Confirmed:** the page states it.
- **Partly supported:** the gist is on the page but a detail (a button label, a time estimate) is not.
- **Not on the cited page:** the claim may be true but its citation doesn't show it. 16 of the 28 were scope descriptions cited to a general page, which led to the scope-on-page rule.

### High-stakes facts confirmed on the provider's own pages (both audits)

- **Gmail**
  - Restricted scopes require an annual third-party security assessment from a Google-approved assessor (support.google.com/cloud/answer/13464321).
  - Verification "might require several months" for sensitive data (answer/7454865).
  - Approved branding must be published within 7 days or it needs re-verification (answer/10311615).
  - Quota is per project and per user per project, so a shared app shares the project quota (developers.google.com/workspace/gmail/api/reference/quota).
  - Security assessments are revalidated every 12 months (answer/13463816).
- **Pipedrive**
  - Only one callback URL per app.
  - App type can't be changed after creation.
  - Testing only works for users in the sandbox account.
  - Sandbox accounts are deleted after 45 days with no app, or six months with no live app.
  - Private apps can be shared with any company via a direct link without review.
- **Linear**
  - App names must be 2–80 characters and can't contain "Linear" or http(s).
  - Access tokens last 24 hours, and refresh has a 30-minute grace period.
  - OAuth apps get 5,000 requests and 2,000,000 complexity points per hour, per user.
  - Admins manage OAuth apps under Settings > Administration > API.

### What was wrong, and what we changed

We fixed the pipeline, never the guides by hand, and regenerated the library.

| Finding | Example | Fix | Result after regenerating |
|---|---|---|---|
| "Own app = your own rate limits" offered as a reason when limits aren't per app | Linear limits are per user; Pipedrive's daily budget is shared by the whole company account | A prompt fix wasn't enough: Linear repeated the error three times. Code rule: a rate-limit or quota reason survives only if it names a per-app, per-client or per-project unit and the cited page says the same | Gone from every guide. A correct one, Gmail's per-project quota, still passes the rule |
| Numbers that aren't on the cited page | Gmail's "100 users" cap cited to a page that never says 100; Pipedrive's "60-minute" token expiry cited to the review page | Code rule: numbers must appear in the text the agent read | Those claims are now uncertain. Synthesized time estimates ("15–30 min") are now honestly uncertain too |
| URLs only *linked* from a page counted as "seen" | A claim could cite a page that no agent ever opened | Only pages read or returned by search count | Closed |
| Branding claim overstated | Google shows your app name on the consent screen only after verification | Prompt: say so when branding needs verification | Partly fixed; depends on the model reading it |
| Third-party price used for a number | CASA cost of $499–$5,400 came from one assessor's own pricing blog | Not enforced in code (see limitations) | Flagged here |

### Found while reviewing the library (before the audit)

| Finding | Evidence | Fix |
|---|---|---|
| **Slack guide missed a major limit.** Since May 2025, Slack apps distributed outside the Marketplace get 1 request per minute and 15 objects per call on `conversations.history` and `conversations.replies`, and the API terms now point commercially distributed apps to the Marketplace. For an AI agent that reads messages, that changes the plan. | docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps | The approval researcher must now search the provider's changelog for limits on unreviewed or unlisted apps. Slack went from **easy / "Same day"** to **medium / "Same day to 12 weeks"** with a "Rate limits on unlisted apps" flag. |
| **Cited a page that no longer exists.** The fetch tool served a cached copy of `pipedrive.readme.io/docs/how-to-create-an-app`, which returns 404 live. | Link check flagged it; 7 claims were downgraded | `read_page` now checks the live URL first and tells the agent to find the current page |
| **Steps tagged tested, and screenshots on the wrong step.** A signup-page screenshot was attached to "Open API settings". | First Linear run | Code rule: steps are never tested. Screenshots moved off steps entirely: public-page screenshots almost never show a step behind the login, so they now appear only under "What our browser saw" |
| **Policy text read in the browser tagged as tested.** | Asana run: "no separate developer account" sourced from a docs page | The portal explorer reports `observations` (tested) and `stated` (sourced) separately |
| **Difficulty flipped between runs.** | Pipedrive: medium, then easy | Rubric rewritten as "the path until any customer can connect", with explicit criteria |
| **A key limit found in one run and missed in the next.** HubSpot apps distributed through the Marketplace are capped at 25 installs until listed (private apps: 10 allowlisted accounts). One run said "Same day" for any customer. | developers.hubspot.com/docs/apps/developer-platform/build-apps/manage-apps-in-hubspot and the Sep 2025 changelog | The approval researcher must always answer two fields, "install cap before review" and "limits until reviewed". The lead writer must fold them into the launch time. All five guides now state both, with sources |
| **Downsides presented as reasons to own an app.** Slack listed "avoid unlisted rate limits" as a reason to own your app; it's really a cost of owning an unlisted one. | Slack guide | Prompt: downsides of owning an app go in pitfalls. Slack now shows it as a pitfall and a flag |

### Where the final library stands

Both errors from the first audit are gone from the shipped guides. Every guide states its install cap and its limits until review, with a source:

| App | Install cap before review | Limits until reviewed |
|---|---|---|
| Linear | none found | 5,000 requests per hour per user (confirmed) |
| Slack | none found | Tier 1 (1 request per minute) on `conversations.history` and `conversations.replies` until Marketplace-approved (confirmed) |
| HubSpot | 25 installs until listed; 10 accounts for private apps (confirmed) | 110 requests per 10 seconds per installing account (confirmed) |
| Gmail | 100 test users in testing mode; 100-user cap when unverified (confirmed) | testing authorizations expire after 7 days (confirmed) |
| Pipedrive | none for live private apps (confirmed) | standard plan limits |

The remaining risk is coverage. Agents can still miss a fact in a given run; the required fields reduce that for the facts that change a launch plan the most.

## 3. What we could not verify

- Anything behind a provider login. The agents never sign up or log in, so setup steps are sourced from docs, never tested.
- Real review times. No app was submitted for review. Waits come from the provider's own estimates.
- Per-scope review levels for Gmail's People API scopes (contacts, birthday, addresses, phone numbers). Composio's default Gmail scopes include them, but the citations point to general verification pages.
- Whether Composio's own managed apps are verified or Marketplace-listed. That would change some "do you need your own app" advice, for example for Slack.

## 4. Model choice (Gmail, same pipeline)

| Lead-writer model | Time | Gemini cost | Claims | Notes |
|---|---|---|---|---|
| `gemini-3.8-flash` | 202s | $0.16 | 51 | More granular approval section, short flags |
| `gemini-3.1-pro-preview` | 230s | $0.26 | 42 | Same sources, broader claims, long flags |

We chose Flash for everything. A final library guide costs $0.12–0.16 in Gemini usage (from LangSmith); Composio tool credits are extra.

## 5. Human setup attempt (Linear)

The assignment asks for checks against real sources *or* setup attempts. Sections 1–3 cover the sources. A hands-on attempt needs a real Linear login, which the agents never do by design (section 3). It has **not been done yet**. The author plans to follow the Linear guide and record the results here.
