# Verification

What we tested, what sources support, what we corrected, and what we still can't verify.
All checks ran on 2026-10-28.

## 1. Checks that run on every guide

Every guide is checked by plain code, not by the model, before it is saved:

| Check | How | Tag it earns |
|---|---|---|
| Does Composio provide an OAuth app? | Composio toolkit API (`composio_managed_auth_schemes`) | tested |
| Does the website load? | HTTP request | tested |
| Is the provider's authorize URL live? | Request with a fake `client_id` and Composio's redirect URI, then classify the response (rejects unknown app / login wall / renders / blocked / 404) | tested |
| What did the developer portal look like? | Browser agent on public pages only, with screenshots copied to our bucket | tested (observations only) |
| Was the cited page actually read? | A "sourced" claim keeps its tag only if its URL was read or returned by search during this run | otherwise uncertain |
| Is the number on the page? | Every number in a sourced claim must appear in the text the agent read at that URL | otherwise uncertain |
| Do the links work? | Every cited URL is fetched; a 404, 410 or DNS failure downgrades the claim | otherwise uncertain |
| Steps and screenshots | A setup step is never "tested" (no one logs in); a screenshot stays on a step only if it shows that step's page; any "redirect" field is forced to Composio's redirect URI | n/a |

These rules live in `app/guide.py` (`gate`, `apply_links`, `classify_probe`) and are exercised by `uv run python -m app.check`.

## 2. Manual audit of three guides (109 sourced claims)

**Method.** For every claim tagged sourced in the Linear, Pipedrive and Gmail guides, we fetched the cited page with the same Composio tool the agents use. We pulled the passages that best match the claim (`verification/source_passages.py`, output in `verification/passages-*.txt`) and read each claim against its page. Where a number or rule mattered, we searched the full page for it. The audited guides are saved as `verification/audited-*.json`.

| Guide | Sourced claims | Confirmed | Partly supported | Not on the cited page | Wrong |
|---|---|---|---|---|---|
| Linear | 31 | 16 | 9 | 5 | 1 |
| Pipedrive | 35 | 15 | 8 | 11 | 1 |
| Gmail | 43 | 15 | 16 | 12 | 0 |
| **Total** | **109** | **46 (42%)** | **33 (30%)** | **28 (26%)** | **2 (2%)** |

- **Confirmed:** the page states it.
- **Partly supported:** the gist is on the page but a detail (a button label, a time estimate) is not.
- **Not on the cited page:** the claim may be true but its citation doesn't show it. 16 of the 28 are scope descriptions cited to a general page instead of the scope reference.

### High-stakes facts we confirmed on the provider's own pages

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
| "Own app = your own rate limits" offered as a reason when limits aren't per app | Linear limits are per user; Pipedrive's daily budget is shared by the whole company account | Prompt: count rate limits only if the provider's limits are per app or project | Fixed for Pipedrive. **Still wrong for Linear** (it says "per OAuth application"; the page says per user). Kept as a known residual error; Gmail correctly keeps it because its quota is per project |
| Numbers that aren't on the cited page | Gmail's "100 users" cap cited to a page that never says 100; Pipedrive's "60-minute" token expiry cited to the review page | Code rule: numbers must appear in the text the agent read | Those claims are now uncertain. Synthesized time estimates ("15–30 min") are now honestly uncertain too |
| URLs only *linked* from a page counted as "seen" | A claim could cite a page that no agent ever opened | Only pages read or returned by search count | Closed |
| Branding claim overstated | Google shows your app name on the consent screen only after verification | Prompt: say so when branding needs verification | Partly fixed; depends on the model reading it |
| Third-party price used for a number | CASA cost of $499–$5,400 came from one assessor's own pricing blog | Not enforced in code (see limitations) | Flagged here |

### Found while reviewing the library (before the audit)

| Finding | Evidence | Fix |
|---|---|---|
| **Slack guide missed a major limit.** Since May 2025, Slack apps distributed outside the Marketplace get 1 request per minute and 15 objects per call on `conversations.history` and `conversations.replies`, and the API terms now point commercially distributed apps to the Marketplace. For an AI agent that reads messages, that changes the plan. | docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps | The approval researcher must now search the provider's changelog for limits on unreviewed or unlisted apps. Slack went from **easy / "Same day"** to **medium / "Same day to 12 weeks"** with a "Rate limits on unlisted apps" flag. |
| **Cited a page that no longer exists.** The fetch tool served a cached copy of `pipedrive.readme.io/docs/how-to-create-an-app`, which returns 404 live. | Link check flagged it; 7 claims were downgraded | `read_page` now checks the live URL first and tells the agent to find the current page |
| **Steps tagged tested, and screenshots on the wrong step.** A signup-page screenshot was attached to "Open API settings". | First Linear run | Code rules: steps are never tested; a screenshot must match its step's page |
| **Policy text read in the browser tagged as tested.** | Asana run: "no separate developer account" sourced from a docs page | The portal explorer reports `observations` (tested) and `stated` (sourced) separately |
| **Difficulty flipped between runs.** | Pipedrive: medium, then easy | Rubric rewritten as "the path until any customer can connect", with explicit criteria |

### Residual errors in the final library

These are known and still there:

- **Linear:** "Dedicated OAuth rate limits" is wrong. Limits are per user, so a shared app doesn't pool them.
- **Slack:** "Avoid unlisted rate limits" is listed as a reason to own your app. It is really a reason to get your app listed in the Marketplace.

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

_Pending: the author follows the Linear guide to create a real OAuth app and records what was wrong, missing or confusing here._
