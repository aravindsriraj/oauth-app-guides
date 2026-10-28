{%- set c = g.composio -%}
{%- macro claim(x, label=True) -%}
- {% if label and x.label %}**{{ x.label }}.** {% endif %}{{ x.text }} _[{{ x.tag }}]_{% if x.source_url %} ({{ x.source_url }}){% endif %}
{%- endmacro -%}
# {{ g.app_name }}: OAuth app setup guide

Researched by agents on {{ g.generated_at[:10] }}. Tags: tested = seen during research, sourced = stated on the linked page, uncertain = check before relying on it.{% if g.trace_url %} Research trace: {{ g.trace_url }}{% endif %}

## Summary

**Difficulty: {{ g.difficulty }}.** {{ g.difficulty_why }}

- **Working for you in development:** {{ g.dev_time.label }}. {{ g.dev_time.text }} _[{{ g.dev_time.tag }}]_
- **Ready for any customer:** {{ g.launch_time.label }}. {{ g.launch_time.text }} _[{{ g.launch_time.tag }}]_
{% if g.flags %}- **Watch out for:** {{ g.flags|join(", ") }}
{% endif %}
**An agent can do:** {% for s in g.steps if s.actor == "agent" %}{{ s.label or s.text }}; {% endfor %}the Composio auth config and test connection; the email to support.
**You take over for:** {% for s in g.steps if s.actor == "you" %}{{ s.label or s.text }}{{ "; " if not loop.last }}{% endfor %}.

## Do you need your own app?

{{ verdict(c, g.app_name) }}

{% for x in g.own_app_reasons %}{{ claim(x) }}
{% endfor %}
## Before you start

{{ claim(g.app_type) }}
{% for x in g.prerequisites %}{{ claim(x) }}
{% endfor %}
## Create the app
{% for s in g.steps %}
{{ loop.index }}. **{{ s.label or "Step " ~ loop.index }}** ({{ "agent can do this" if s.actor == "agent" else "you" }}){% if s.url %} {{ s.url }}{% endif %}
   {{ s.text }} _[{{ s.tag }}]_{% if s.source_url %} ({{ s.source_url }}){% endif %}
{% for f in s.fields %}   - {{ f.label }}: `{{ f.value }}`
{% endfor %}{% if s.screenshot %}   ![Screenshot]({{ s.screenshot }})
{% endif %}{% endfor %}
## Permissions

{% for s in g.scopes %}- `{{ s.label }}`: review = {{ s.review }}. {{ s.text }} _[{{ s.tag }}]_{% if s.source_url %} ({{ s.source_url }}){% endif %}
{% else %}No scope-level review rules found.
{% endfor %}
## Getting approved

{% for x in g.approval %}{{ claim(x) }}
{% endfor %}
## Pitfalls

{% for x in g.pitfalls %}{{ claim(x) }}
{% endfor %}
## When you're stuck

{% for x in g.support %}{{ claim(x) }}
{% endfor %}
**Email to {{ g.app_name }} support**

Subject: {{ g.email_subject }}

{{ g.email_body }}

## Connect it to Composio

{% if c and "OAUTH2" in c.auth_modes -%}
{% if c.managed %}You can skip this for now: Composio's managed app works today.

{% endif %}1. In the Composio dashboard, open Auth Configs, create one for {{ g.app_name }} (OAuth2) and paste your Client ID and Secret.
2. Make sure {{ g.app_name }}'s app settings list this redirect URI: `{{ c.redirect_uri }}`
3. Pass the auth config ID in sessions: `composio.create(user_id="user_123", auth_configs={"{{ c.toolkit }}": "ac_your_config_id"})`
4. Test with `session.authorize("{{ c.toolkit }}")`; the consent screen should show your app's name.

Source: https://docs.composio.dev/docs/authentication/custom-app-vs-managed-app
{%- elif c -%}
Composio connects {{ g.app_name }} with {{ c.auth_modes|join(", ") }}, so there's no OAuth app to plug in.
{%- else -%}
Composio has no {{ g.app_name }} toolkit yet.
{%- endif %}

## Sources and gaps

**Checked by our own code:**
{% for x in g.checks %}{{ claim(x) }}
{% endfor %}
**What we couldn't verify:**
{% for x in claims(g) if x.tag == "uncertain" %}- {{ x.text }}
{% else %}Every claim has a source.
{% endfor %}
**Pages used:** {% for u in g.links|sort %}{{ u }}{{ ", " if not loop.last }}{% endfor %}
