# Corect.uk

A lightweight British English assistant for Romanian speakers. Django templates with locally served HTMX and Chart.js provide an accessible, responsive interface; OpenAI runs exclusively on the server. Learners type or speak in English or Romanian and press one button, „Vreau să sune natural!”: English comes back corrected (with explanations) and, when it helps, in a more natural British phrasing; Romanian comes back as natural British English. Every result can be heard in a British accent. Staff get a usage analytics area covering registered users, anonymous visitors, real token and audio usage, and estimated AI cost by source.

## Run locally

Requires Python 3.11+ and PostgreSQL 16 (or Docker Desktop). Commands below run from this directory.

### 1. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
```

If PowerShell activation is unavailable, use `.\.venv\Scripts\python` instead of `python` in the commands below.

### 2. Install dependencies and configure the environment

```sh
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in PowerShell, or `cp .env.example .env` on macOS/Linux). Do not overwrite an existing configuration.

Generate a Django secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and place it in `DJANGO_SECRET_KEY`.

Set `OPENAI_API_KEY` to your API key and `OPENAI_MODEL` to a Responses API model that supports Structured Outputs (the project runs with `gpt-5.6-luna`). Both are required for live results; the application still starts and displays a friendly unavailable message without them. Voice uses the same key with the defaults `gpt-live-transcribe` (live transcription), `gpt-transcribe` (finished-recording fallback) and `gpt-4o-mini-tts`. There is no fake production AI fallback.

`.env` is read when the server process starts. After changing it, stop and restart `runserver`; the auto-reloader does not pick up `.env` changes.

### 3. Start PostgreSQL

```sh
docker compose up -d --wait
```

Compose uses a dedicated persistent volume and binds PostgreSQL to `127.0.0.1:5434`, avoiding common local database ports. The example password is only for local development.

Alternatively, create a PostgreSQL role and database yourself, then set `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_HOST` and `DATABASE_PORT`. Django always uses PostgreSQL; there is no SQLite fallback. The test role needs permission to create a test database (`CREATEDB`); the Compose role has this permission. A production role should not.

### 4. Initialise and run

```sh
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Visit <http://127.0.0.1:8000/>. Admin is at `/admin/` and usage analytics at <http://127.0.0.1:8000/admin/analytics/> (staff or superuser accounts only). Creating a superuser is optional for ordinary use; signup is available in the application. No preconfigured application accounts are shipped.

`migrate` also runs the analytics backfill (each existing saved request gets one usage ledger row, see [Historical data](#historical-data)) and creates the audio usage ledger (`analytics.0003_audiousageevent`). `analytics.0005_audio_stt_mode` marks earlier transcriptions as finished-recording (`file`) transcriptions without changing their costs, and `assistant.0002_realtime_transcription_session` adds the operational table for live sessions. There is no historical audio usage before the ledger, so nothing audio-related is backfilled. The backfill can be reversed with `python manage.py migrate analytics 0001`.

The microphone needs a secure context: `http://127.0.0.1` / `localhost` or HTTPS. Browsers block microphone access on plain HTTP from other hosts.

### Sharing a local server through a tunnel (ngrok)

Add the tunnel host to `DJANGO_ALLOWED_HOSTS` and its HTTPS origin to `DJANGO_CSRF_TRUSTED_ORIGINS`, then restart the server:

```sh
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,your-name.ngrok-free.dev
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-name.ngrok-free.dev
```

Without the trusted origin, forms and voice requests fail CSRF checks because HTTPS ends at the tunnel. Forwarded headers are still not trusted.

## Configuration

| Variable | Meaning / default |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required random secret; keep private |
| `DJANGO_DEBUG` | `false` by default; example enables local development |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts, default `localhost,127.0.0.1` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated full origins (e.g. `https://name.ngrok-free.dev`) for tunnels or proxies that end HTTPS in front of Django; empty by default |
| `SITE_URL` | The public HTTPS origin, e.g. `https://corect.uk` (no trailing slash), used for canonical links, Open Graph/Twitter URLs, `robots.txt` and `sitemap.xml`. Required when `DJANGO_DEBUG=false` (system check `core.E004` refuses empty, `http`, localhost or a path); locally, empty uses the request's own origin |
| `CLIENT_IP_HEADER` | Which header carries the visitor's address when the request comes from a trusted proxy: `none` (default, `REMOTE_ADDR` only), `x-forwarded-for`, `x-real-ip` or `cf-connecting-ip`. Anything else, or a header without `TRUSTED_PROXY_CIDRS`, stops the app at startup (see [Deployment behind a proxy](#deployment-behind-a-proxy)) |
| `TRUSTED_PROXY_CIDRS` | Comma-separated IPv4/IPv6 addresses or networks of your own reverse proxies, empty by default. Forwarding headers from any other address are ignored; `0.0.0.0/0` and `::/0` are refused |
| `DATABASE_NAME`, `DATABASE_USER` | Default `englishcoach` |
| `DATABASE_PASSWORD` | Required database password |
| `DATABASE_HOST`, `DATABASE_PORT` | Default `127.0.0.1`, `5434` |
| `OPENAI_API_KEY` | Server-only credential, used for text and voice |
| `OPENAI_MODEL` | Explicit text model selection, no hidden default |
| `OPENAI_TIMEOUT` | Provider timeout for text and voice, default 60 seconds (long English full of mistakes can take 25–35 seconds) |
| `OPENAI_KEEPALIVE_SECONDS` | How long the shared OpenAI connection stays open between requests, default 20 (httpx closes idle connections after 5 s otherwise, so a request a few seconds after the previous one would pay a new TLS handshake) |
| `OPENAI_REASONING_EFFORT` | Empty by default (the model's own default). `minimal`, `low`, `medium` or `high` sends that `reasoning.effort` with every text request; change it only after running the language-quality eval |
| `OPENAI_PRICING` | Optional JSON text price table; empty uses the built-in `gpt-5.6-luna` prices, `{}` disables text cost estimates (see [Token tracking and pricing](#token-tracking-and-pricing)) |
| `VOICE_REALTIME_ENABLED` | `true` by default: words appear in the text box while the learner speaks. `false` is the kill switch: every browser records, stops and uses `OPENAI_TRANSCRIBE_MODEL` |
| `OPENAI_LIVE_TRANSCRIBE_MODEL` | Live (realtime) speech-to-text model, default `gpt-live-transcribe` |
| `OPENAI_TRANSCRIBE_MODEL` | Finished-recording speech-to-text model (fallback), default `gpt-transcribe` |
| `OPENAI_TTS_MODEL` | Text-to-speech model, default `gpt-4o-mini-tts` |
| `OPENAI_TTS_VOICE` | Voice for British speech, default `cedar` |
| `OPENAI_TTS_SPEED` | Speech speed from 0.25 to 4.0, default `1.0` (invalid values stop the app at startup) |
| `OPENAI_AUDIO_PRICING` | Optional JSON audio price table; empty uses the built-in prices, `{}` leaves audio costs unknown (see [Audio pricing](#audio-pricing)) |
| `VOICE_MAX_SECONDS` | Live transcription and recording limit, default 60 (also caps metered live duration) |
| `VOICE_MAX_BYTES` | Upload limit for one recording, default 5,000,000 bytes (the provider allows 25 MB) |
| `VOICE_TRANSCRIBE_LANGUAGES` | Language hints sent to transcription, default `en,ro`; empty sends none. Mixed-language speech still works |
| `VOICE_REALTIME_DELAY` | Live transcription `delay`, default `low`; empty sends none. See [Latency](#latency) before changing it |
| `VOICE_TRAILING_AUDIO_MS` | After Stop, how long the microphone stays on for words already being spoken before the audio is committed, 0–1500 (see [Latency](#latency) for the measured default). Not used after a silence stop or the character limit |
| `VOICE_FINAL_TRANSCRIPT_MS` | Safety limit for waiting for the final transcript after the commit, default 2500 (500–4000). The wait normally ends as soon as the provider's final event arrives |
| `VOICE_TRANSCRIBE_LIMIT_MINUTE`, `VOICE_TRANSCRIBE_DAY_LIMITS` | Speech-to-text guardrail (technical, never the plan quota): default 5 per minute per actor, and per London day `20,80,400` for anonymous, Free and Pro; live sessions and finished recordings share it |
| `VOICE_TTS_LIMIT_MINUTE`, `VOICE_TTS_DAY_LIMITS` | British speech guardrail: default 10 per minute per actor, and per London day `30,120,600` for anonymous, Free and Pro |
| `VOICE_SPEECH_TOKEN_MAX_AGE` | Lifetime of a result's signed speech token in seconds, default 2700 (45 minutes) |
| `ANALYTICS_GBP_PER_USD` | Pounds per US dollar used to show costs in £ in the staff analytics and admin, default `0.74` (GBP/USD 1.3510 on 11 September 2026). Ledgers and price tables stay in USD, the currency OpenAI bills in; invalid values stop the app at startup |
| `ANALYTICS_VISITOR_COOKIE` | `true` by default; `false` stops setting the anonymous visitor cookie |
| `ASSISTANT_MAX_CHARACTERS` | Maximum input length, default 2000 |
| `NATURALIZE_ANONYMOUS_LIMIT_DAY`, `NATURALIZE_FREE_LIMIT_DAY`, `NATURALIZE_PRO_LIMIT_DAY` | The plan quota: successful „Vreau să sune natural!” results per London calendar day, default 5, 20 and 200 (Pro's Fair Use ceiling). Each must be at least 1 and they must not decrease from anonymous to Free to Pro, or the app stops at startup (see [Plans and daily limits](#plans-and-daily-limits)) |
| `NATURALIZE_RATE_LIMIT_MINUTE` | Technical rate limit, not the plan: „Vreau să sune natural!” requests per minute per actor, default 10. `RATE_LIMIT_MINUTE` is still read as its old name; `RATE_LIMIT_DAY`, `VOICE_TRANSCRIBE_LIMIT_DAY` and `VOICE_TTS_LIMIT_DAY` are no longer used, and `manage.py check` warns (`core.W001`) while they are set |
| `CONTENT_MODERATION_ENABLED`, `OPENAI_MODERATION_MODEL` | `true` and `omni-moderation-latest` by default: every submitted text is checked with OpenAI's free moderation endpoint, in parallel with the model call (no extra waiting), and refused with `content_blocked` when flagged, except when the only flags are plain `violence`/`illicit` (`MODERATION_ALLOWED`: injuries, accidents, news; graphic violence, violent illicit activity, threats, hate, harassment, sexual content and self-harm stay refused); fails closed (`moderation_unavailable`) when the check cannot run |
| `CONTACT_EMAIL` | Public contact address; empty by default, which hides the Contact page (404) and its footer link |
| `PRO_DISPLAY_PRICE` | Normal Pro price shown on the homepage plans, default `£9.99`. Display only: nothing is charged or enforced |
| `PRO_PROMO_ENABLED`, `PRO_PROMO_PRICE` | `true` and `£4.99` by default: the normal price is shown struck through beside the promotional price per month; `false` shows only the normal price |
| `LEGAL_OPERATOR_TYPE` | `sole_trader` (an individual trading as Corect.uk, the default) or `company` |
| `LEGAL_OPERATOR_NAME`, `LEGAL_SERVICE_ADDRESS` | Legal operator and correspondence address for the Terms, Privacy notice and Contact page; empty by default and never invented. Required, together with `CONTACT_EMAIL`, when `DJANGO_DEBUG=false` (system check `core.E001`–`E003`) |
| `LEGAL_TRADING_NAME`, `LEGAL_JURISDICTION`, `LEGAL_COUNTRY` | Default `Corect.uk`, `England and Wales`, `United Kingdom` |
| `COMPANY_NUMBER`, `VAT_NUMBER` | Shown only when set (company number only for `company`) |
| `LEGAL_HOSTING_PROVIDER` | Optional; named as a recipient in the Privacy notice |
| `OPENAI_LEARNING_MODEL` | Model for learning content (exercise batches, open-answer checks); defaults to `OPENAI_MODEL`. Priced from `OPENAI_PRICING`, so add a price row when it differs |
| `LEARNING_REUSE_THRESHOLD` | Default 7: while a learner has at least this many (or enough for the session) unused stored exercises for a pattern, no new batch is generated |
| `LEARNING_BATCH_SIZE` | Default 8 exercises per generation call |
| `LEARNING_AI_RATE_LIMIT_MINUTE` | Learning AI calls (exercise generation, open-answer checks) per minute per account, default 6 |
| `LEARNING_AI_DAY_LIMITS` | Learning AI calls per London day per account, `Free,Pro`, default `40,200` (Free gets Pro's while `FREE_HAS_PRO_FEATURES` is on) |
| `LEARNING_AI_GLOBAL_DAY_CALLS`, `LEARNING_AI_GLOBAL_DAY_COST_USD` | Service-wide ceilings for learning AI per London day: 2000 calls and 10 USD of recorded spend by default. See [Learning AI cost guardrails](#learning-ai-cost-guardrails) |
| `PRO_ENTITLEMENTS_ENFORCED` | `false` by default: every signed-in user gets the learning features. `true` limits the features in `apps/core/entitlements.py:PRO_FEATURES` to the "Pro" group |
| `LOG_FORMAT`, `LOG_LEVEL` | `json` (one object per line, the default with `DJANGO_DEBUG=false`) or `text` (the default locally); level `INFO`. See [Monitoring](#monitoring) |
| `SENTRY_DSN` | Optional error tracking; empty (the default) leaves Sentry off. Nothing personal is sent (see [Monitoring](#monitoring)) |
| `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`, `SENTRY_TRACES_SAMPLE_RATE` | Sentry labels, and the share of requests traced for performance, 0–1, default `0` |
| `AI_COST_ALERT_DAILY_USD`, `AI_COST_ALERT_HOURLY_USD` | `check_production_health` alerts when AI spend (text, voice and learning, USD) passes these: default 20 per London day and 5 in the last hour. The dashboard warns from 80 % of the daily figure |
| `ALERT_ERROR_RATE_PERCENT`, `ALERT_MIN_REQUESTS` | Alert when provider failures reach this share of AI requests in the last hour (default 20 %), once at least this many requests were made (default 20) |

An actor is a signed-in user or an HMAC (keyed with `DJANGO_SECRET_KEY`) of an anonymous visitor's IP address; an IPv6 visitor counts as its /64 network, because one connection usually controls a whole /64. The raw address is never stored. Forwarding headers are read only from the proxies in `TRUSTED_PROXY_CIDRS`, and only the one named by `CLIENT_IP_HEADER` (`apps/core/client_ip.py`); behind an unconfigured proxy all anonymous visitors would share the proxy's quota, see [Deployment behind a proxy](#deployment-behind-a-proxy). This rate-limiting identity is separate from the analytics visitor ID described below. Voice calls count in their own namespaced counters (`voice-stt:` and `voice-tts:`), so they never use up the plan quota. Opening a live transcription session counts as one speech-to-text call, the same as uploading a finished recording, so opening and closing connections cannot bypass the limit.

## Architecture and behaviour

- `core`: homepage, shared layout, footer, privacy, terms and contact pages (there is no settings page). The public interface is in Romanian (`LANGUAGE_CODE = "ro"`, so Django's own form labels, validation messages and dates are Romanian too); `apps/core/middleware.py` keeps `/admin/` and the staff analytics in English. Learning content stays in English: corrections, natural versions, British English made from Romanian and practice questions. There is one main button, „Vreau să sune natural!”, and no language selector: the learner writes or speaks English or Romanian and the server decides what to do. The header (logo, menu, user icon) stays sticky on every screen size. Desktop shows every page in the top menu; mobile uses a hamburger that opens a slide-in drawer with an icon per page. The user icon links to sign-in when signed out and to the profile when signed in (the profile is not repeated in the menus). On desktop the blue editor panel stays sticky beside a long result; on mobile the homepage fits one screen without scrolling, and "Text nou" (plus "Autentificare / Creează cont" when signed out) appears below a result. Before anything is sent, the result card shows four large icon tiles describing what the learner gets (mistakes fixed and explained, a natural English alternative, British pronunciation with one click, progress, history and exercises). The text box contains the microphone button; while the box is empty, its character counter reads "Scrie în acest ecran sau vorbește aici" instead of "0/2000", and five everyday British English example sentences are gently typed and erased in it (`static/js/example-prompts.js`). Each contains a phrase translated word for word from Romanian ("I have delayed with ten minutes", "make us a photo"): once typed, that phrase is struck through in red and its natural British English ("I'm ten minutes late", "take a photo of us") is typed beside it in green. The examples are a decorative, `aria-hidden` layer over the textarea, never its value, so they are never counted or submitted; they disappear while the box is focused, has text, the microphone is in use or a request is running, return about two seconds after the box is empty and idle again, and are shown as one still sentence when reduced motion is preferred. Status messages (voice states, "Conversie în text natural…") are announced to screen readers only, so nothing is written under the text box. The button keeps its label and look while a request runs (the result card shows the loading state); repeat presses during a request are ignored.
- Homepage design (`templates/core/home.html`, `static/css/home.css`, `landing.css`):
  - **Page width and header.** Every page uses the same shell as the homepage (1170px, so 1090px of content) and the same header: 915px wide and centred on desktop, frosted, with an "Începe acum" button for signed-out visitors (`app.css`). The homepage hero and editor line up with the header.
  - **Hero (desktop).** The one-line title „Vorbește natural limba engleză” with „limba engleză” in blue, the lead, three value points and the Westminster picture fading into the page.
  - **Hero picture.** It comes from `HERO_ART` in `apps/core/views.py` (`static/img/hero.webp`, 1774×887). To replace it, change the path and the pixel size there. It is shown from 1024px only; below that a 1 × 1 transparent `<source>` stops phones and tablets from downloading it.
  - **Desktop editor area.** A centred column with the blue writing panel ("Mod Politicos" switch, text box, microphone, button) beside the benefit panel, which is the empty result card. Once a result appears the result column widens.
  - **Phones and tablets.** The page fits one screen and opens straight on the editor: the hero (picture, title text, value points) is hidden, keeping its `h1` for screen readers, and the text box takes whatever height is left. Below it the four benefits are compact 2 × 2 cards, then a one-line "De ce să alegi Corect.uk" card linking to `/about/`, then only the footer links. Every section below the editor (features, how it works, plans, the strip and the closing banner) is shown from 1024px (`.landing-wide-only`) and on `/about/` at every width. After a result, "Text nou" (and sign-in when signed out), the "De ce să alegi Corect.uk" card and the footer follow it without a gap; the extra scroll room that lets a short result reach the top of the screen is added after the footer (`--result-tail`, measured in `static/js/assistant.js`). The old `/despre/` address redirects permanently to `/about/`.
- Homepage landing sections (from 1024px wide; "Cum funcționează" at every width): below the editor, "De ce să alegi Corect.uk" (12 features; the 5 not included in Free carry a "Pro" corner ribbon), "Cum funcționează" (4 steps), "Alege planul potrivit" (Free and Pro), a short plan strip and a closing banner with a single action ("Creează cont", or "Scrie primul text" when signed in). They live in `templates/core/partials/desktop_*.html` and `static/css/landing.css`, are server-rendered and use no JavaScript; below 1024px they are `display: none`, so phones and tablets keep the editor-first homepage. **Nothing is charged yet**: the copy is in `apps/core/plans.py` (the daily limits in it come from settings), the Pro price comes from `PRO_DISPLAY_PRICE`, "Alege Pro" is a disabled button marked as coming soon, and no payments or subscriptions exist yet; the daily limits of each plan are enforced (see [Plans and daily limits](#plans-and-daily-limits)). To show a user the Pro view, add them to the "Pro" group in `/admin/` (created by migration `core.0001_pro_group`, checked by `is_pro()` in `apps/core/plans.py`): the plans section then reads "Ești în planul potrivit." with only the Pro benefits list. The group also gives the account Pro's daily limit (200 under Fair Use); nothing is charged. The site footer (`templates/partials/site_footer.html`) appears on every page and screen size, including below the mobile homepage, with Istoric (signed in only), Confidențialitate (`/confidentialitate/`), Termeni (`/termeni/`) and Contact (`/contact/`, only when `CONTACT_EMAIL` is set). The privacy and terms pages describe the current behaviour in plain language and have not been reviewed by a lawyer; review them before public launch.
- `accounts`: signup, sign-in with a username or an email address (`EmailBackend`, only when the email belongs to exactly one account), sign-out, and a profile page to change username, email and password (the session stays signed in after a password change) or delete the account with password confirmation (history, corrections and acceptance records are deleted; visitor IDs linked to the account or the browser are deleted, so the remaining usage ledger rows carry neither user nor visitor). Signup requires an unticked "Confirm că am cel puțin 16 ani și accept Termenii și Politica de confidențialitate" checkbox and records a `LegalAcceptance` (user, `TERMS_VERSION`, `PRIVACY_VERSION`, timestamp, source).
- Legal and consent (`apps/core/legal.py`, `consent.py`, `checks.py`): the operator's identity comes only from settings; Terms (`/termeni/`, with a `#fair-use` section) and the Privacy notice (`/confidentialitate/`) are versioned by `TERMS_VERSION`/`PRIVACY_VERSION` in `config/settings.py`. On a first visit, or after either version changes, a discreet bar at the bottom of every page says that using Corect.uk means accepting the Terms and being at least 16, links the Terms and the Privacy notice, and has a single "Am înțeles" button (no tick box; nothing is blocked or refused meanwhile). Pressing it stores the versions in the `corect_consent` cookie (without reloading the page when JavaScript is on); for signed-in users it also records a `LegalAcceptance` for the current versions, and they see the bar until their account has one. The footer's "Setări cookie-uri" link opens the analytics setting in a dialog (or `/cookie-uri/` without JavaScript). See `LEGAL_LAUNCH_CHECKLIST.md` for the owner actions still required.
- `assistant`: the source-language registry (`languages.py`), Pydantic schemas, the versioned prompt, `NaturalizeService` (`services/naturalize.py`), the shared OpenAI client (`services/provider.py`), the OpenAI adapter with moderation (returning the parsed result together with provider usage), request timings (`services/timing.py`), result presentation (`presentation.py`), the language-quality eval (`evals/`), voice services and endpoints (`services/voice.py`, `services/realtime.py`, `voice_views.py`), pricing, persistence and database-backed limits.
- `learning`: the personal learning engine and its pages (see [Learning engine](#learning-engine)): the `/learn/` dashboard, personalised practice sessions, insight-led progress (the 30-day Chart.js chart stays below, with its daily-counts table as the accessible fallback), mistake pages by pattern and category, and private history (grouped by day, newest first: each day collapses and opens, the most recent day starts open, and pages hold whole days, 7 per page). All learning pages share a sidebar (Panou, Progres, Greșeli, Istoric, in the same order as the header menus; the page titles are the same short words); below 1024px it is replaced by the header drawer and the page becomes one column.
- `analytics`: the append-only text usage ledger (`UsageEvent`), the append-only audio usage ledger (`AudioUsageEvent`), anonymous visitors (`AnonymousVisitor`), the staff dashboard under `/admin/analytics/`, read-only admin registrations and the `recalculate_usage_costs` command.

`POST /naturalize/` accepts `text`, `submission_token` (UUID), and Django's CSRF token; there is no operation or language field. Normal submissions return the homepage; `HX-Request: true` returns result HTML plus out-of-band updates for a fresh submission token and the actions below the result. Error codes are 400 (validation), 409 (duplicate), 422 (unsupported language), 429 (quota) and 503 (service failure). GET never calls AI. The former `/assistant/correct/` and `/assistant/translate/` URLs no longer exist (404).

### One action, one AI call

Every accepted submission makes **at most one** generative call: `NaturalizeService.naturalize(text)` sends one `responses.parse` with the static `NATURALIZE_PROMPT` and the `NaturalizeResult` schema. Moderation runs at the same time on a small shared thread pool and its verdict wins. There is no separate language-detection call, no second call for Romanian and no local language-detection library.

1. The model first sets `source_language` (`en`, `ro`, or `other`/`ambiguous`), then follows that language's rules in the same response: English gets `corrected_text`, `corrections` and an optional `natural_text`; Romanian gets the natural British English in `natural_text`.
2. `operation_for(source_language)` maps the language to an internal operation from the registry: `en` → `correction`, `ro` → `translation`. `other`/`ambiguous` returns the `language` error ("Scrie în engleză sau română ca să primești varianta naturală în engleză britanică.").
3. A finaliser turns the provider schema into the result type that history, learning and analytics already understand: `CorrectionResult` for English (every correction guard below applies) or `TranslationResult` for Romanian (`invalid_translation` when the English is empty or the same as the input). The provider schema has no cross-field validators, so a model slip never discards a billed response before the guards run.
4. The result card shows one of four outcomes (`apps/assistant/presentation.py:result_outcome`): **errors** ("Engleza ta, corectată", the Greșit/Corect/Explicație cards and "Sună mai natural:" when there is a natural version), **unnatural** (correct English, a prominent "Sună mai natural:" and "✓ Engleza ta e corectă."), **natural** ("✓ Sună deja natural.") and **translated** ("În engleză britanică"). Old history rows translated from English into Romanian still render ("Engleză → română"). Every English sentence in a result (corrected, natural or translated) ends with a discreet copy icon at its bottom right (`assistant/copy_button.html`, `static/js/copy.js`, loaded on every page so history entries have it too); it copies just that sentence.

The internal operations `correction` and `translation` stay in the database (`AssistantRequest.request_type`, `UsageEvent.request_type`) for history, learning, analytics and cost; the learner never sees them. Only English creates `GrammarCorrection` rows and learning occurrences. Existing history is shown unchanged, including rows created by the old buttons.

Request options: `store=False`, `max_output_tokens` from `output_token_budget(text)` (3000 minimum, +2 per character, 8000 maximum: 7000 at 2000 characters, measured so the heaviest eval response uses about 60 % of it), `prompt_cache_key="corect:naturalize:<PROMPT_VERSION>"` (the instructions are static, so the provider's prompt cache serves about 99 % of input tokens) and `reasoning.effort` only when `OPENAI_REASONING_EFFORT` is set. Only `prompt_cache_key` and `reasoning` are accepted as extra options by `parse_response`. Automatic SDK retries are disabled; requests are stateless. No history is sent as context. The provider's own data policies still apply; `store=False` is not a promise of zero provider retention. `store=False` is set on every `responses.parse` call (naturalisation and learning, through `parse_response`); the moderation, transcription, realtime and speech endpoints have no such option, so the Privacy notice says only what this configuration can support. Whether Zero Data Retention or a different retention period applies to the account is an OpenAI organisation/contract setting to confirm with OpenAI (LEGAL_LAUNCH_CHECKLIST.md), not something this code can promise.

**Mod Politicos.** A switch in the top right of the writing panel (`<input type="checkbox" name="polite" role="switch">`, remembered per browser in `localStorage`). When it is on, the same single call uses `NATURALIZE_POLITE_PROMPT`: the normal prompt plus `POLITE_RULES`, with its own cache key (`corect:naturalize-polite:<version>`), so the switch never travels inside the learner's text. The rules:
- English is still corrected exactly as usual (only genuine errors), but a natural version is always given, even for text that is already correct and natural. It is the whole text as a polite, considerate British speaker would say it: softened requests and refusals ("Could you…, please?", "I'm afraid…"), the same meaning, facts, relationship and roughly the same length. It is never pompous, flowery or grovelling, and adds no greetings or information.
- Romanian is first naturalised intent-first, exactly as in normal mode (see "Romanian behaviour" below), then given the same polite attitude; it never falls back to a literal translation.

The result still shows „Sună mai natural:”; a request costs the same one daily use. `UsageEvent.polite` records the switch (dashboard card "Mod Politicos", admin filter), and `prompt_version` gets `+polite` on requests and usage events. The live eval has a `polite` group (`EvalCase.polite`).

**Shared client.** `apps/assistant/services/provider.py:openai_client()` builds one OpenAI client per process (thread-safe, behind a lock, rebuilt only when the key, timeout or keep-alive setting changes) with its own connection pool and `OPENAI_KEEPALIVE_SECONDS`. Text, moderation, learning AI, live-transcription secrets, file transcription and text to speech all reuse it, so a request does not pay a new TLS handshake. It is never used as a context manager, which would close it for every other request.

Correction behaviour:

- **Minimal correction.** `corrected_text` fixes only genuine errors, so the learner sees exactly what was wrong. When the model reports no genuine errors, correct English is restored unchanged (apart from silent capitalisation and punctuation fixes).
- **Silent capitalisation and punctuation.** Capital letters, full stops, commas and similar marks are fixed in the corrected and native text but never listed as corrections, mentioned in explanations, highlighted in the sentence comparison or stored as mistakes.
- **Tense versus time expression.** A verb tense that contradicts a time expression ("I was there tomorrow") is always corrected, keeping the time expression; the explanation mentions the alternative of changing the time word.
- **Natural version.** An optional `natural_text` (stored as `native_text`, so old history keeps working) shows how a British English speaker would naturally phrase the text (word order, prepositions, collocations, British vocabulary such as "shop" for "store"), with a short Romanian note. It is shown only when it differs from the correction by more than capitals and punctuation, including for text that is grammatically correct but unnatural.
- **British preferences.** British spelling suggestions remain separate, do not alter otherwise correct American English in the corrected text, and never count as mistakes.
- **Snippets must exist.** Every `original` snippet must occur in the learner's text (`invalid_snippet` otherwise); a correction that changes nothing fails with `invalid_correction`.
- **Input normalisation.** Browser line breaks (CRLF) become LF and outer whitespace is trimmed before the provider call. The model does not echo the original text (saving output tokens); the server stores the learner's own text.

Romanian behaviour: **intent-first naturalisation, not literal translation.** The model first works out what the Romanian speaker wants to communicate (an offer, a request, an invitation, a refusal, an update) and to whom, then writes what a native British speaker would naturally say or write to do the same thing, in contemporary British English. It may restructure the sentence (word order, construction, splitting or joining sentences, leaving out what English leaves implicit) and uses established collocations, phrasal verbs, idioms and contractions where they fit: "Vrei să mergi cu mine cu mașina diseară, la 5?" becomes "Would you like a lift at five this evening?", not "Do you want to come with me in the car this evening at five?". Meaning comes before idiom: facts, people, times, dates, quantities, names, places, conditions, negation, certainty, the kind of sentence (question, request or statement), tone and register are kept, and nothing the Romanian does not state or clearly imply is added ("Vrei să vii cu mine diseară la 5?" mentions no car, so it gets no lift). A WhatsApp message stays casual; a message to a manager, landlord, school, the council or a GP surgery is professional; a formal letter stays formal; and a plain request stays plain, because systematic softening is Mod Politicos. There are no phrase rules in Python: the prompt's ordered principles and examples guide the model, which reads the context. Text without diacritics, with cedilla (ş/ţ) or comma (ș/ț) diacritics and with English words mixed in is supported. Explanations are always in Romanian. Linguistic quality still depends on the configured model and prompt; schema validation cannot prove grammar quality, which is why the [language-quality eval](#language-quality-eval) exists.

Only authenticated successful requests save text/results. Grammar corrections are saved atomically with their parent request. Anonymous text/results are not written to the database. Authenticated failures store a sanitised code and metadata without text. User deletion cascades to saved requests. History and statistics always filter on the current authenticated user.

Atomic PostgreSQL updates prevent parallel requests from exceeding a rate limit or a plan quota. A unique actor/submission token prevents duplicate provider calls across workers. Claims are retained for seven days; expired counters and older claims should be cleaned daily:

```sh
python manage.py cleanup_assistant
```

The same command closes live transcription sessions the browser never finished (closed tab, lost network), writing one ledger row each with an unknown cost, and deletes closed session rows after seven days. It also deletes expired sign-in sessions (as `clearsessions` would). It never deletes usage ledger rows or visitors. The retention periods live in `apps/assistant/retention.py`, which the Privacy notice reads, so the published periods match what the command enforces.

The prompt is versioned in `apps/assistant/services/prompts.py` (currently `2026-09-v8-naturalize`, the single-action prompt with intent-first Romanian); the version is saved with every request and usage event and is part of the prompt cache key. Changing it requires running the language-quality eval.

### Source languages

`apps/assistant/languages.py` is the only place that knows which input languages exist. Each `SourceLanguage` has a code, an English name for the prompt, a Romanian label and its internal operation; `TARGET` is British English (`en-GB`, stored with the legacy code `en`), and explanations are in `EXPLANATION_LANGUAGE` (Romanian). The schema's `source_language` values, the per-language prompt sections, the voice transcription prompt and the unsupported-language message are all generated from it, and a test fails if code elsewhere compares a language code directly (`== "ro"`).

To add a source language (none is planned for now):

1. Add a `SourceLanguage` to `SOURCE_LANGUAGES` and its rules to `LANGUAGE_RULES` in `prompts.py`; raise `PROMPT_VERSION`.
2. Add its code to `VOICE_TRANSCRIBE_LANGUAGES` if the transcription model supports it.
3. Add eval cases for the language to `apps/assistant/evals/naturalize_cases.jsonl` and run the live eval: routing must stay at or above 98 % and the existing groups must not get worse.
4. Review the Romanian copy that names the languages ("Scrie în română sau engleză…", the unsupported-language message, the Privacy notice) and raise `PRIVACY_VERSION` if the notice changes.

## Plans and daily limits

The product unit is one successful „Vreau să sune natural!” result. Each plan tier has one daily allowance, whatever the language and however the text was entered:

| Tier | Who | Daily limit |
| --- | --- | --- |
| Anonymous | Not signed in | 5 |
| Free | Signed-in account | 20 |
| Pro | Account in the "Pro" group | 200, the Fair Use ceiling (shown as "Cereri nelimitate (Fair Use)"; the Terms state the ceiling) |

- **One resolver.** `apps/core/plans.py:tier_for(user)` decides the tier (not signed in → anonymous; `is_pro` → pro; otherwise free). The limits come from `NATURALIZE_DAILY_LIMITS` in settings, validated at startup; the public copy (plans, Terms, messages) reads the same numbers.
- **What counts.** Typed, pasted and spoken text each count 1 when submitted; English and Romanian count the same. Opening the microphone, live transcription, the recording fallback and British speech (including replays) never count. `/naturalize/` does not know, or ask, how the text was entered.
- **Reserve, commit, release** (`apps/assistant/services/quota.py`, table `NaturalizeUsage`). After the suspension, per-minute rate limit and duplicate-token checks, one use is reserved with a single conditional `UPDATE`, so the last use of the day goes to exactly one of several simultaneous requests. It is committed only when a result is produced and released on any failure: moderation or instruction refusal, unsupported language, provider timeout or error, invalid output, or a database error before a usable result. A duplicate submission (double click, retry with the same token) is refused before anything is reserved. A reservation left by a process that stopped mid-request is given back after `OPENAI_TIMEOUT` plus 5 minutes.
- **Days.** London calendar days (`TIME_ZONE = "Europe/London"`, `apps/assistant/services/localday.py`): the allowance resets at local midnight, so the days the clocks change are 23 or 25 hours long, and `Retry-After` counts down to that midnight.
- **What people see.** When the allowance is used up the result card shows an alert. Anonymous visitors are invited to create a free account (20 a day); Free users are told that Pro gives unlimited requests under Fair Use („Cu Pro ai cereri nelimitate, în regim Fair Use.”), with a link to the plans; Pro users are told the Fair Use limit resets at midnight UK time. Anonymous and Free results show a small „3 din 5 utilizări rămase astăzi”, and the profile shows the plan and what is left today; Pro is not counted down. History, progress, mistakes, practice pages and speech already loaded in the page keep working.
- **Technical guardrails stay separate** (`apps/assistant/services/limits.py`, table `RateBucket`): `NATURALIZE_RATE_LIMIT_MINUTE` against bursts, and speech-to-text and British speech limits per minute and per London day by tier (`VOICE_TRANSCRIBE_DAY_LIMITS`, `VOICE_TTS_DAY_LIMITS`), set above what each plan's naturalisations need so voice never blocks legitimate use.
- **Anonymous identity.** The anonymous allowance is kept per keyed hash of `REMOTE_ADDR` (never the IP itself, never forwarded headers). It deters abuse; it is not an identity: people sharing one network share one allowance, and a new network starts a new one. An account has its own allowance.
- **Retention.** `cleanup_assistant` deletes counters two days after their day (`NATURALIZE_USAGE_DAYS`); the Privacy notice reads the same value.

## Learning engine

Python decides **what** a learner needs; AI only writes **content**, when nothing suitable is stored. Everything lives in `apps/learning/` (`services/`, `models.py`, `taxonomy.py`), and every threshold is a named constant in `services/rules.py`.

**From correction to profile (no extra AI call).** Each correction the model returns carries a `pattern` from the fixed taxonomy in `apps/learning/taxonomy.py` (about 47 keys such as `since_vs_for`, `missing_article`, `third_person_s`, `base_form_after_did`, plus `<category>_other`); a pattern that does not belong to the correction's category becomes `<category>_other`. When a signed-in user's correction is saved, `services/profile.py:record_correction_occurrences` links each genuine correction to a `MistakeOccurrence` (British preferences are never mistakes) and recalculates that user's `UserMistakePattern` rows. The result then shows, under a repeated mistake, "Ai mai făcut această greșeală de N ori." with an "Exersează acum" button that starts a session for that pattern. A database failure here is logged (`learning_profile_unavailable`) and never breaks the correction.

**Status, priority and review** (deterministic):

| Rule | Definition |
| --- | --- |
| Recurring | at least 2 occurrences in the last 30 days, or 3 in total |
| Improving | at least 3 recent attempts with 60 % correct, and fewer occurrences than the previous 30 days (or none for 14 days) |
| Mastered | at least 5 recent attempts with 80 % correct and no occurrence for 21 days |
| Resurfaced | a mastered pattern that occurs again |
| Priority | 3 × recent occurrences + min(total, 10) + 2 × recent failures − 1.5 × recent successes, + 5 if resurfaced, + up to 3.5 when the review is overdue, + 4 if seen in the last 7 days; × 0.2 when mastered |
| Review | after a session: 80 % or more moves the pattern to the next interval (1, 3, 7, 14, 30 days); below 50 % goes back two steps and returns tomorrow; mastered patterns are reinforced every 45 days |

"Pentru tine azi" (`services/daily.py`) picks up to 5 exercises from the top three patterns (2 + 2 + 1, due reviews first), with the reason and an estimated time. Time-dependent scores are refreshed at most hourly when a learning page opens.

**Exercises.** Practice never asks for a whole sentence: every exercise is multiple choice, a choice of phrase, or one sentence with a single blank for a word or short phrase (at most five words), typed on one line into the gap. The structured output allows only those three types, `clean_batch` drops a blank that is missing, repeated or answered with a sentence, and older stored `rewrite`/`short_correction` exercises are no longer chosen for sessions. `Exercise` rows are stored per learner and reused. `ensure_exercises` generates one batch (`LEARNING_BATCH_SIZE`) only when fewer than the session needs **and** fewer than `LEARNING_REUSE_THRESHOLD` unused ones are stored; exercises answered in the last 7 days rest, and generated ones retire after two correct answers, four showings or 90 days. The editorial bank (`apps/learning/practice.py`) is seeded as shared exercises and fills any gap, so practice keeps working when AI is unavailable. Answers are graded in Python (option index, or normalised text against the answer and accepted answers); only an open rewrite left in a session started before short answers (none are created now) is sent to AI, through the abuse guardrails, and is shown as unverified (not counted) if that check fails.

**Starting and finishing practice.** Starting a session prepares its exercises on the server (an AI call of several seconds when a batch is needed), so every form that starts practice carries `data-wait="Se pregătește exercițiul…"`: `static/js/wait-screen.js` then shows the site's one waiting screen (`.wait-screen`, the frosted white screen with a spinner that the microphone also uses) until the next page loads, and ignores a second press. A finished session ends with a short report (`services/report.py:session_report`, no AI): the score as a ring, a verdict with up to three stars, correct / to review / unverified counts, the streak of days with finished practice, each pattern's mastery before and after the session (`PracticeSession.mastery_at_start`, taken when the session starts) with its status and next review, up to five wrong answers beside the correct ones, and what to do next ("Repetă exercițiile" or "Exersează din nou", back to Greșeli or the dashboard, Progress).

**AI calls** go only through `services/ai.py:learning_call`, which refuses suspended accounts, sends minimal JSON (pattern, at most three truncated recent examples; never the history, username or email), validates the structured output (Pydantic, fewer than 3 usable exercises counts as `invalid_output`) and records exactly one `LearningUsageEvent` per call, including failures. Prompts and their versions are in `services/prompts.py` (`2026-09-learn-practice-v2`, `2026-09-learn-answer-v1`). Insights ("Te-ai îmbunătățit", "Încă se repetă", "Aproape rezolvat", "A revenit") are database aggregates and appear only with enough data. On Progress, "Ce s-a schimbat" (`templates/learning/_changes.html`, data from `insights.py:change_cards`) draws them as cards with small SVG charts from the same data: occurrences per week over the last 8 weeks for a pattern, and for an almost solved one its practice accuracy as a ring plus its last five results as dots. The arrow on a card opens that category's mistakes.

**Existing data.** Build occurrences and patterns from corrections saved before this feature (idempotent, no AI, corrections unchanged):

```sh
python manage.py rebuild_learning_profiles
python manage.py rebuild_learning_profiles --user 42
```

Older corrections without a stored pattern get one from `derive_pattern` (word-difference rules). The learning profile is deleted with the account, and the Privacy notice describes it.

### Learning AI cost guardrails

Every learning AI call goes through `apps/learning/services/guardrails.py` before the provider is contacted. Two levels protect the budget:

- **Per account**: `LEARNING_AI_RATE_LIMIT_MINUTE` a minute and `LEARNING_AI_DAY_LIMITS` a day (by plan), against one account looping or scripting requests.
- **Service-wide**: `LEARNING_AI_GLOBAL_DAY_CALLS` (an exact counter) and `LEARNING_AI_GLOBAL_DAY_COST_USD` (today's recorded learning spend), against a bug, a bot or a crowd. The spend cap is read from the usage ledger, so calls already running when it is reached can finish and pass it by those calls; the call counter cannot be passed.

Counters live in the same database buckets as the other guardrails and are counted in one transaction. A call that later fails still counts, so a failing provider cannot be retried in a loop for free. If the counters cannot be read, no AI call is made. A prevented call is recorded as a refused `LearningUsageEvent` (`learning_rate_limit`, `learning_quota_exhausted` or `learning_budget_exhausted`, no tokens, no cost) and shown in the staff dashboard, which raises an alert when the service-wide budget is hit. Learners keep practising: generation falls back to stored and editorial exercises with a short explanation, and open answers stay unverified ("verificarea automată … este oprită pentru azi"). Limits are read from the environment at start-up; change them and restart, no deploy needed.

Answering the same exercise twice in one session (double click, second tab) is graded and counted once: the first submission claims the answer atomically.

## Voice

Voice has two parts: speaking into the text box, and listening to the correction in British English. Corect.uk supports **realtime speech to text**: words appear in the text box while the learner is still speaking. Realtime applies **only to transcription**. It is not a voice assistant: nothing is spoken back, nothing is corrected while speaking, and the text is processed only after the learner presses „Vreau să sune natural!”, through the normal text flow (spoken Romanian is sent exactly like typed Romanian). British speech is a separate feature, generated only when a speaker button is pressed. No browser `SpeechRecognition` or `SpeechSynthesis` is used.

### Voice input: live transcription

1. The learner presses the microphone inside the text box. Pressing (`pointerdown`, or Enter/Space) first adds a `preconnect` hint for `https://api.openai.com`, never on page load. The browser asks for the microphone once (`getUserMedia`). Until the connection is listening (usually a few seconds), the site's waiting screen (frosted white with a spinner, `.wait-screen`) says "Pornim microfonul…", so nobody starts speaking too early. While listening, the button shows a stop square instead of the microphone.
2. The browser calls `POST /assistant/realtime-transcription/session/` (CSRF protected, counted against the speech-to-text guardrail, never against the plan's daily naturalisations). When the Permissions API already reports the microphone as `granted`, this request starts at the same time as `getUserMedia`, and the peer connection, data channel and SDP offer are prepared while it is in flight; if the microphone then fails, the session is closed at once as `realtime_connect_failed` (0 s, $0). When permission is `prompt` or `denied`, the request waits for the learner to allow the microphone, so a refusal never uses quota. Django asks OpenAI for a short-lived client secret (`client.realtime.client_secrets.create`, i.e. `POST /v1/realtime/client_secrets`, usable for 30 seconds to start a session) for a **transcription-only** session whose configuration is fixed on the server: `type: "transcription"`, model `OPENAI_LIVE_TRANSCRIBE_MODEL`, `languages` from `VOICE_TRANSCRIBE_LANGUAGES`, the prompt "The speaker may use English or Romanian, or mix them in the same recording." (generated from the language registry), `delay` from `VOICE_REALTIME_DELAY`, near-field noise reduction and no turn detection. No keywords are sent. Nothing in the request body is read, so a visitor cannot choose another model or session type. The response contains only the client secret, its expiry, an opaque signed session token, the OpenAI calls URL and the time limit; `OPENAI_API_KEY` never reaches the browser.
3. The browser opens a WebRTC connection directly to OpenAI (`POST https://api.openai.com/v1/realtime/calls` with the SDP offer and `Authorization: Bearer <client secret>`), sends the microphone track and listens on the `oai-events` data channel. Audio never passes through Django; there is no WebSocket proxy.
4. Each `conversation.item.input_audio_transcription.delta` event updates the text box immediately. The box is rebuilt every time as *text before the caret + transcript + text after the caret* (`static/js/transcript-state.js`): deltas extend their item, a `conversation.item.input_audio_transcription.completed` transcript replaces that item's text, and items are keyed by `item_id` (ordered with `previous_item_id`), so revisions never duplicate words or leave stale text. Speech is inserted where the caret was (a selection is replaced) with sensible spacing, the character counter updates live and the box scrolls as text grows. While words are arriving the text box is read-only (still readable, scrollable and focusable) and „Vreau să sune natural!” is disabled.
5. The session ends when the learner presses the stop button, after 3 seconds of silence (counted from the moment it starts listening and restarted by every new word, so it also stops when nothing is said at all), or at `VOICE_MAX_SECONDS`. OpenAI rejects turn detection (server VAD) for `gpt-live-transcribe` ("Turn detection is not supported for this transcription model"), so silence is detected in the browser from the transcript stream. After the stop button, the microphone stays **on** for `VOICE_TRAILING_AUDIO_MS` so a word still being spoken is not cut; after a silence stop or the character limit there is nothing left to catch, so that wait is skipped. The browser then mutes the microphone, sends `input_audio_buffer.commit` and waits for the provider's final event: the wait ends the moment `…transcription.completed` (or `…failed`, an error or a lost connection) arrives, with `VOICE_FINAL_TRANSCRIPT_MS` only as a safety limit. It then closes the connection and stops every microphone track. The text stays, editing is restored immediately and nothing is submitted.
6. The browser sends `POST /assistant/realtime-transcription/finish/` with the signed session token, how the session ended, the provider-reported duration and whole-millisecond timings (see [Latency](#latency)); never any text. The server writes exactly one ledger row for the session, however often finish is called.

Limits and failures:

- **Character limit.** Every delta is checked against `ASSISTANT_MAX_CHARACTERS` together with the existing text. A delta that would not fit stops the microphone, keeps everything that fitted and never leaves half a word; the learner sees "Ai ajuns la limita de 2.000 de caractere. Am oprit microfonul."
- **Time limit.** The browser stops at `VOICE_MAX_SECONDS`. OpenAI transcription sessions have no maximum-duration setting, and a client secret only limits *starting* a session, so on the server side the limit is enforced through the short secret lifetime, the per-actor session quota and the metering cap described in [Audio pricing](#audio-pricing).
- **Connection lost while speaking** (network, provider error, microphone lost): the text received so far is kept and the learner sees "Conexiunea pentru transcriere live s-a întrerupt. Am păstrat textul primit până acum." The audio is **not** sent again to file transcription, so speech is never billed twice; the learner can retry.
- **Fallback.** If live transcription is switched off (`VOICE_REALTIME_ENABLED=false`, the production kill switch), unsupported (no WebRTC), or cannot connect before any speech is transcribed (session refused, offer rejected, data channel not open within 10 seconds), the browser records on the same microphone stream and transcribes the finished recording (below). A working live session never also uses file transcription. A rate-limited session (429) shows the limit message instead of falling back.
- **Closing the page** closes the connection, stops the microphone and sends the finish request with `navigator.sendBeacon`. Sessions the browser never finishes are closed by `cleanup_assistant`.
- **Accessibility.** The microphone keeps its 44×44px target and announces "Înregistrează-ți vocea", "Pornim transcrierea live" and "Oprește transcrierea live" through its label and `aria-pressed`. Nothing is written under the text box: a visually hidden live region announces state changes and errors to screen readers only ("Ascult… Vorbește normal. Textul apare pe măsură ce vorbești.", "Gata. Poți modifica textul, apoi apasă „Vreau să sune natural!”."), never individual words; the text box is the transcript. On touch screens the text box is not focused after the stop, so the keyboard does not cover the new text.
- **Browsers.** Live transcription uses feature detection for `RTCPeerConnection` and `getUserMedia` (current Chrome, Edge, Firefox and Safari, on HTTPS or localhost); anything else uses the fallback. Automated tests run in desktop and mobile-sized Chromium only; check a real iPhone (Safari) and Android phone (Chrome) before release.

### Voice input fallback: finished recording

1. The browser records with `MediaRecorder`, choosing the first supported format from `audio/webm;codecs=opus`, `audio/webm`, `audio/mp4`, `audio/wav`.
2. Pressing the microphone again stops the recording; it also stops automatically after `VOICE_MAX_SECONDS`. Every microphone track is stopped as soon as recording ends. Closing the page discards the recording instead of uploading it.
3. The recording is uploaded to `POST /assistant/transcribe/` (CSRF protected) and sent to `OPENAI_TRANSCRIBE_MODEL` with English and Romanian hints (`languages`) and a prompt saying the speaker may mix both. One language is never forced, so English, Romanian and mixed speech all work.
4. The transcript is inserted at the caret, with sensible spacing, and the character counter updates. Nothing is sent automatically: the learner checks the text, then presses „Vreau să sune natural!”. If the result would exceed `ASSISTANT_MAX_CHARACTERS`, nothing is inserted and a message explains why.

The server identifies the container from the file's first bytes (WebM, MP4/M4A, WAV or MP3) and requires the declared content type to match; the content type alone is never trusted. Ogg recordings are not accepted because OpenAI does not document them for transcription. Oversized bodies are refused before they are read, and the upload is held in memory only (never a temporary file) up to `VOICE_MAX_BYTES`. Errors return a short message: permission denied, unsupported browser, recording too long, "We couldn't understand that recording", or voice temporarily unavailable. In this mode the microphone button announces its state (record, stop recording, transcribing) through `aria-pressed`, its label and a live region.

### Voice output (British text to speech)

The corrected sentence, the unchanged sentence of a correct text, the natural version and the British English made from Romanian each have a speaker button ("Ascultă varianta corectă în engleză britanică", "Ascultă varianta naturală în engleză britanică", "Ascultă în engleză britanică"). Romanian text, explanations, old translations into Romanian and anything else never get one. Nothing plays automatically.

Pressing a speaker sends its token to `POST /assistant/speech/`, which calls `OPENAI_TTS_MODEL` with the configured `OPENAI_TTS_VOICE` and `OPENAI_TTS_SPEED`, MP3 output and server-sent events, and always these instructions (`BRITISH_TTS_INSTRUCTIONS` in `apps/assistant/services/voice.py`, the only copy):

> Speak in natural contemporary British English with a neutral Southern British accent. Use British pronunciation, stress and intonation. Sound warm, clear and conversational. Do not use an American accent. Do not exaggerate Received Pronunciation. Speak at a normal, learner-friendly conversational pace without sounding slow, theatrical or robotic. Read exactly the supplied English sentence and add no commentary.

Only one sentence plays at a time; pressing another speaker stops the current one, and pressing a playing speaker pauses it. The returned audio is cached in browser memory for the page, so replaying the same sentence makes no new paid request. Speaker buttons use event delegation and keep working after HTMX replaces the result.

**No open text-to-speech proxy.** When a result is rendered, each speakable sentence gets a Django-signed token (`signing.dumps`, salt `corect.speech.v1`) containing the approved English text, the target (`correction`, `native` or `translation`) and, for a live correction, its usage event ID. The speech endpoint accepts only that token, verifies the signature and its age (`VOICE_SPEECH_TOKEN_MAX_AGE`), and never reads a raw `text` parameter. Tampered, foreign, expired or wrong-target tokens are refused before any provider call. Rendering a result never calls text to speech: 100 corrections with no Listen clicks cost nothing for audio.

### Voice privacy

- Raw audio, uploaded filenames, transcripts, speech text, generated audio and provider payloads are never stored or logged, for anonymous and registered users alike. Audio exists only in request memory while it is sent to the provider; generated MP3 is returned directly with `Cache-Control: no-store`.
- Transcription never adds anything to history. A registered user's text is saved only when they actually press „Vreau să sune natural!”, exactly as before.
- Live transcription: transcript deltas and final transcripts exist only in the browser page; provider events are never sent to or logged by Django, and the finish request carries no text. The client secret is returned once with `Cache-Control: no-store` and never stored or logged. `RealtimeTranscriptionSession` keeps only who opened a session (user or visitor, audience), the model, its status and times: no audio, transcript, client secret, OpenAI session or call ID, or IP address. Closed session rows are deleted after seven days; ledger rows are kept.
- Logs contain sanitised codes only (`voice_failed code=... operation=...`), never token contents or exception text.
- Anonymous voice usage is attributed to the same `AnonymousVisitor` cookie as text usage (created on first use if needed), or recorded without a visitor when `ANALYTICS_VISITOR_COOKIE=false`. No new identity system is added.

Sanitised audio error codes include `realtime_timeout`, `realtime_unavailable`, `realtime_session_invalid`, `realtime_session_expired`, ledger codes `realtime_interrupted`, `realtime_page_closed`, `realtime_connect_failed` and `realtime_abandoned`, `microphone_upload_invalid`, `audio_too_large`, `unsupported_audio`, `transcription_timeout`, `transcription_failed`, `transcription_empty`, `transcript_too_long`, `speech_token_invalid`, `speech_token_expired`, `tts_timeout`, `tts_failed`, `audio_rate_limit` and `voice_not_configured`.

## Usage analytics (staff)

Open <http://127.0.0.1:8000/admin/analytics/> (in production: `https://<your-host>/admin/analytics/`). Only authenticated staff or superuser accounts can view it; everyone else is redirected to the admin sign-in. Make an account staff with `python manage.py createsuperuser` or by ticking "Staff status" on the user in `/admin/`.

| Page | What it shows |
| --- | --- |
| `/admin/analytics/` | The business funnel (below), AI cost by source (all sources, text AI, audio, voice input split into realtime and file transcription with sessions/calls, minutes and cost, voice output, each with its share), registered users, anonymous visitors, signup conversion, text requests (period, today, 7 and 30 days), successes, failures, rejections, corrections, translations, voice transcriptions, British TTS plays, text tokens, text activity chart, text errors, text models, audio usage, audio models, audio errors, top users and top visitors |
| `/admin/analytics/users/` | Every registered user with joined date, last activity, text requests, corrections, translations, successes, failures, text tokens, voice transcriptions, realtime sessions, realtime audio, TTS plays, text AI cost, realtime STT cost, file STT cost, voice input cost, voice output cost, audio cost and total AI cost; sortable, searchable by username or email, paginated |
| `/admin/analytics/users/<id>/` | One user's cost by source, text usage by period, audio usage, models, errors, daily activity and linked anonymous visitors |
| `/admin/analytics/visitors/` | Anonymous visitors (`anon-7e5238`) with text or voice activity: first/last seen, active days, text requests, voice transcriptions, TTS plays, costs by source and conversion; searchable by short or full ID |
| `/admin/analytics/visitors/<uuid>/` | One visitor's cost by source, anonymous text and voice usage, requests before and after conversion, errors and daily activity; never text or audio |

Filters: period (today, 7, 30, 90 days, all time), audience (all, registered, anonymous), request type (English correction, Into British English, Unclassified; applies to text figures only) and model (text and audio models, shown when more than one has been used). “—” means not recorded: pricing unavailable, or history saved before token tracking.

### Business funnel

The dashboard's **Business funnel** section answers "are people coming, using Corect, coming back and interested in paying?" for today, the last 7 days and the last 30 days (London days; the report filters do not apply). It is first-party and follows the analytics consent: nothing is recorded for a visitor who switched analytics off, and no third-party analytics is used.

| Step | Stable name / source | Recorded by |
| --- | --- | --- |
| Visitor | `site_visit` | `static/js/funnel.js`, once a day per browser (bots and scripts without JavaScript are not counted) |
| Uses Corect | successful `UsageEvent` (existing) | — |
| Returns | `site_visit` on two or more days in the window | — |
| Starts signup | `signup_viewed` | signup page |
| Creates account | `User.date_joined` (existing) | — |
| Hits the Free / anonymous limit | `UsageEvent` `quota_exhausted` (existing) | — |
| Sees the plans | `pricing_viewed` (placement `home` / `about`) | the plans section scrolled into view |
| Clicks Pro | `pro_cta_clicked` (placement `quota_box`; `pricing_section` once the plan card's Pro button is live) | the link or button's `data-funnel-cta` |
| Starts checkout / pays | `checkout_started`, `subscription_started` | `apps.analytics.services.funnel.record_funnel_event(request, FunnelEvent.Name.CHECKOUT_STARTED)` from future billing code; shown as "billing not live" until data exists |

`FunnelEvent` rows hold only the step name, a short placement, the audience and plan, the account or anonymous visitor ID and the day: no text, results, audio, IP address, email or user agent. Each step is stored at most once per identity, placement and day (unique constraints, so repeats and concurrent requests are harmless). The browser posts steps to `POST /analytics/event/` (CSRF-protected, allow-listed names and placements, 30 per minute per actor, always `204`). Rows are deleted with their account or visitor ID and after about 13 months by `cleanup_assistant` (`FUNNEL_EVENT_DAYS`). Visitors are counted as accounts plus anonymous visitors, so a browser that signs up counts once as each.

### Where the AI cost comes from

| Figure | Source |
| --- | --- |
| Text AI cost | Sum of `UsageEvent.estimated_cost` („Vreau să sune natural!” via `OPENAI_MODEL`) |
| Realtime speech to text | Sum of `AudioUsageEvent.estimated_cost` where `operation = transcription` and `stt_mode = realtime` (`OPENAI_LIVE_TRANSCRIBE_MODEL`) |
| File speech to text (fallback) | The same where `stt_mode = file` (`OPENAI_TRANSCRIBE_MODEL`) |
| Voice input (speech to text) | Realtime + file speech to text |
| Voice output (British TTS) | Sum of `AudioUsageEvent.estimated_cost` where `operation = speech` (`OPENAI_TTS_MODEL`) |
| Audio cost | Voice input + voice output |
| Learning AI cost | Sum of `LearningUsageEvent.estimated_cost` (practice batches, open-answer checks via `OPENAI_LEARNING_MODEL`) |
| AI cost · all sources | Text AI cost + learning AI cost + audio cost |

So the grand total is text AI + learning AI + realtime speech to text + file speech to text + British TTS. Learning AI has its own section on the dashboard (calls, tokens, USD and £ per feature, cost per active learner, failures) and its own column on the users list and user detail; learning calls are never counted as text requests. All amounts on the analytics pages and in the admin cost columns are shown in pounds, converted from the USD ledger values at `ANALYTICS_GBP_PER_USD` (the rate is printed above the cost cards); prices and stored costs remain in USD. Each source's percentage is its share of the all-sources total; the audio usage table also shows audio as a share of the total and realtime speech to text as a share of audio, and how many voice input durations were provider-reported or server-observed. Unknown costs are left out of the sums rather than counted as zero, and the pages show how many calls lack pricing or usage. Wherever a column says "Text AI cost" it means „Vreau să sune natural!” submissions only; "Total AI cost" always includes audio.

How requests are counted:

- One `UsageEvent` per submission that passes form validation. Invalid forms (400) are not counted.
- `status` is `success`, `failed` (after acceptance, with a sanitised `error_code` such as `timeout`, `invalid_or_failed_response`, `refused`, `language`) or `rejected` (`quota_exhausted`, `rate_limit`, `duplicate` or a guardrail refusal, before any provider call, so 0 tokens and $0). The success rate is successes ÷ (successes + failures).
- `request_type` is the **effective** operation: `correction` for English, `translation` for Romanian into British English, and `unclassified` when the request ended before the model classified the language (rate limit, duplicate, moderation refusal, timeout). `source_language` holds the detected code (`en`, `ro`, or `other`/`ambiguous` for unsupported text, recorded as `unclassified`). Every submission has at most one text provider call, so `provider_calls` is 0 or 1. `auto_translated` is a legacy field kept for rows from before the single action; migration `analytics.0008_effective_operation_backfill` turned those rows into `translation`/`ro` and copied `source_language` from the saved request, without touching history.
- `duration_ms` is the whole request as the server saw it, `provider_duration_ms` the model call and `moderation_duration_ms` the moderation check (whole milliseconds; no text).
- `plan` is the tier at request time (`anonymous`, `free` or `pro`), so an account that later upgrades keeps its earlier Free rows. Registered rows from before plan quotas stay blank (migration `analytics.0009_plan_tier` fills in only anonymous rows); audio rows carry the plan too. The dashboard's "Plans and daily quota" table shows, per plan, successful naturalisations, quota rejections, how many accounts (Free, Pro) or visitors with analytics on (anonymous) used up the day's allowance and their share of active ones, and how many anonymous visitors signed up or signed in after using theirs. The admin lists filter by plan and error code.
- `model` is the configured `OPENAI_MODEL`; `response_model` is the name the provider returned.
- One `AudioUsageEvent` per live transcription **session** (never one per word, delta or provider message), per finished-recording transcription and per speech request that reaches the quota check. A live session is `success` when it completed or stopped at the character limit; `failed` with `realtime_interrupted` or `realtime_page_closed` (audio was streamed, so its metered cost is kept), `realtime_connect_failed` (no audio, 0 s, $0) or `realtime_abandoned` (finished by cleanup, cost unknown); `rejected` for `audio_rate_limit`. Fields: `operation` (`transcription` or `speech`), `stt_mode` (shown as transcription mode: `realtime` or `file`, empty for speech), `metering_source` (`provider` or `stream_duration`), `speech_target` (`correction`, `native` or `translation`), `model`, `voice`, `status` (`success`, `failed` after a provider call or empty transcript, `rejected` for `audio_rate_limit` at $0), `error_code`, input/output/total tokens, provider-reported `audio_seconds`, `estimated_cost`, audience, user or visitor, and the related `UsageEvent` when a speaker from a live result was pressed. Provider calls also store `provider_duration_ms`; live sessions store the browser's timings `mic_ms`, `session_ms`, `connect_ms`, `startup_ms`, `first_word_ms` and `finalise_ms` and whether the final event arrived (`final_received`), validated as whole numbers up to 600000 and kept from the first finish only. Malformed uploads and invalid tokens are refused without a ledger row. It has no fields for text, transcripts, audio or IP addresses.
- One `LearningUsageEvent` per learning AI call (`feature`: `practice`, `open_answer`), with status (`success`, `failed`, `rejected` for guardrail refusals), sanitised error code, model, response model, prompt version, tokens and estimated cost, and the user. It has no text fields. Generated exercises link to their event. The request type filter does not apply to it.
- The ledgers are append-only: the Django admin shows them read-only, they cannot be added or edited, and only superusers can delete them. Registered text events link to the saved `AssistantRequest` when there is one.
- Every figure is a database aggregate. Reports use conditional aggregation, correlated subqueries for per-row audio figures (so text and audio totals never multiply each other), indexes on both ledgers' time, user, visitor, audience, status, operation/type and model columns, and pagination.

The Django admin also lists text usage events (identity, type, status, error, model, tokens, text AI cost), audio usage events (id, time, operation, transcription mode, audience, user or visitor, speech target, model, voice, status, error, duration, metering source, tokens, audio AI cost; filters including transcription mode and metering source, and search by username, email or visitor ID), anonymous visitors, and saved requests with their token and cost columns, all read-only.

## Anonymous visitors and privacy

- **On by default, with an opt-out.** Anonymous analytics is active unless the visitor unticks "Permit analytics anonim" in "Setări cookie-uri" (`/cookie-uri/`, footer). The setting is stored in the necessary `corect_consent` cookie (Terms/Privacy versions shown plus `0`/`1`, no personal data, 12 months). Every helper in `apps/analytics/services/visitors.py` checks `apps.core.consent.analytics_allowed`, so once switched off no visitor is created, linked or set as a cookie, and events are recorded without a visitor; switching off deletes `corect_visitor_id` from the browser and it is never regenerated. Default-on analytics is a product decision that needs legal confirmation (see `LEGAL_LAUNCH_CHECKLIST.md`).
- Unless switched off, the first time a signed-out visitor presses „Vreau să sune natural!”, the microphone or a speaker, the app creates an `AnonymousVisitor` with a random UUID4 and sets the first-party cookie `corect_visitor_id` (HttpOnly, SameSite=Lax, Secure when `DJANGO_DEBUG=false`, 12 months, refreshed on each anonymous request). Plain page views set no cookie.
- The UUID contains no personal data. An unknown or malformed cookie value is never adopted; a new random ID replaces it.
- Analytics never store IP addresses (raw or hashed) or any submitted, transcribed or generated text or audio. For anonymous visitors only the visitor ID, request type or audio operation, model, voice, prompt version, status/error code, token counts, audio duration, estimated cost and timestamps are kept. Registered history keeps working exactly as before.
- Rate limiting still uses the HMAC of `REMOTE_ADDR`; the analytics ID is not used for limits.
- **Conversion.** When a visitor with the cookie signs up, the visitor is linked to the new account (`converted_via=signup`); signing in to an existing account links it with `converted_via=login`. The first conversion wins; nothing is copied into the account's history and no events are duplicated. Events made after signing in carry the user and, while the cookie exists, the same visitor ID. The dashboard's signup conversion is visitors who signed up ÷ visitors first seen in the period.
- Deleting a user keeps their usage events for aggregate reporting but removes the link (`user` becomes empty); their saved history is deleted as before.
- The privacy notice (`/confidentialitate/`) describes the cookie and the opt-in. Set `ANALYTICS_VISITOR_COOKIE=false` to stop visitor tracking entirely (the analytics choice is then not offered; requests are still counted, without a visitor).

## Token tracking and pricing

`parse_response` returns the structured result together with the provider's reported usage: input tokens, cached input tokens, output tokens (which include reasoning tokens, also stored separately) and total tokens. Usage is recorded as soon as the provider responds, so tokens billed for a response later rejected (incomplete, refusal, failed checks) are still counted. When no response arrives (timeout, connection failure) tokens are unknown. Tokens are never estimated locally.

The estimated cost of each text event is calculated when it is recorded, from `settings.OPENAI_PRICING` in `apps/assistant/services/pricing.py`:

```
cost = ((input − cached) × input_per_1m + cached × cached_input_per_1m + output × output_per_1m) ÷ 1,000,000
```

Built-in text prices (USD per 1M tokens, OpenAI standard tier, <https://developers.openai.com/api/docs/pricing>, checked 13 September 2026): `gpt-5.6-luna` input $0.20, cached input $0.02, output $1.20. Long-context tiers are not modelled; inputs are capped at 2,000 characters.

To change or add text prices, set `OPENAI_PRICING` in `.env` to a JSON object. It replaces the built-in table, and invalid values stop the app at startup:

```sh
OPENAI_PRICING={"gpt-5.6-luna": {"input_per_1m": "0.20", "cached_input_per_1m": "0.02", "output_per_1m": "1.20"}}
```

A model without prices still records tokens; its cost stays empty and shows “—”. After adding prices, fill in missing text costs from the recorded tokens (existing costs are never changed):

```sh
python manage.py recalculate_usage_costs
python manage.py recalculate_usage_costs --model gpt-5.6-luna
```

### Audio pricing

Audio uses its own price table, `settings.OPENAI_AUDIO_PRICING`, parsed by `parse_audio_pricing`; the text table is unchanged. Costs come only from provider-reported usage and are never estimated from file size or text length:

- **Realtime speech to text:** `cost = audio_seconds ÷ 60 × per_minute`. OpenAI reports the billed duration in each `…transcription.completed` event (`usage` of type `duration`, whole seconds counted from the connection to the commit). That event reaches the browser, not Django, so the browser relays the number and the server accepts it only when it is no longer than the session window the server itself observed (session start to finish, plus 2 seconds) and the cap of `VOICE_MAX_SECONDS` + 10 seconds; it is then stored with `metering_source = provider`. Otherwise (no final transcript, interrupted, page closed, an invalid or too-large number) the server-observed window, capped the same way, is stored with `metering_source = stream_duration`: an upper bound, never labelled as provider-reported. A connection that failed before audio records 0 seconds. Sessions closed by cleanup have unknown duration and cost ("—"). A modified browser could under-report its own duration but never exceed what the server observed.
- **File speech to text:** the transcription response's `usage` of type `duration` gives the audio seconds, read by the server (`metering_source = provider`). `cost = seconds ÷ 60 × per_minute`. If a model reports token usage instead, its `input_per_1m`/`output_per_1m` prices are used when configured.
- **Text to speech:** the speech request uses server-sent events; the final `speech.audio.done` event's `usage` gives input and audio output tokens. `cost = (input_tokens × input_per_1m + output_tokens × output_per_1m) ÷ 1,000,000`. If that event is missing, the audio is still returned and usage stays unknown.

Built-in audio prices (USD, <https://developers.openai.com/api/docs/pricing>, checked 13 September 2026): `gpt-live-transcribe` $0.017 per minute of realtime audio; `gpt-transcribe` $0.0045 per audio minute; `gpt-4o-mini-tts` $0.60 per 1M text input tokens and $12.00 per 1M audio output tokens. Live transcription costs about 3.78 times as much per minute as finished-recording transcription, but still only about $0.017 for a full one-minute session. To change them, set `OPENAI_AUDIO_PRICING` (it replaces the built-in table; a model missing from it gets an unknown cost, and `{}` leaves all audio costs unknown while usage is still recorded; invalid values stop the app at startup):

```sh
OPENAI_AUDIO_PRICING={"gpt-live-transcribe": {"per_minute": "0.017"}, "gpt-transcribe": {"per_minute": "0.0045"}, "gpt-4o-mini-tts": {"input_per_1m": "0.60", "output_per_1m": "12.00"}}
```

### Historical data

Migration `analytics.0002_backfill_assistant_requests` creates one ledger row for each `AssistantRequest` saved before tracking existed, copying only exact facts: user, request type, status, error code, model, prompt version and the original timestamp (`is_backfilled=True`). Their tokens and cost stay unknown and are never reconstructed from text length. Anonymous usage before this feature was never stored, so it cannot be backfilled. There is no historical audio usage and none is backfilled. The migration skips requests that already have a ledger row, and reversing it removes only backfilled rows. `analytics.0005_audio_stt_mode` sets `stt_mode = file` on every transcription recorded before live transcription existed (and `metering_source = provider` where a duration was recorded); their costs are not changed.

## Provider cost

Measured on 13 September 2026 at the prices above. The analytics dashboard reports the real totals by source.

| Request | Cost per request | Per 1,000 requests |
| --- | --- | --- |
| Short sentence, already correct | $0.00013 | $0.13 |
| Short sentence with errors | $0.00027 | $0.27 |
| Medium message (~160 characters) | $0.00088 | $0.88 |
| Long message (~620 characters) | $0.0028–0.0035 | $2.84–3.48 |
| Romanian into British English (one call since 15 September 2026; two before) | $0.00014 | $0.14 |
| Voice input, 4–6 second recording (`gpt-transcribe`) | $0.0003–0.00045 | $0.30–0.45 |
| British TTS of one 12-word sentence (`gpt-4o-mini-tts`, 84 input / 101 audio tokens) | $0.0013 | $1.26 |

Output tokens dominate text cost: the ~1,250-token instructions are almost entirely served from the provider's prompt cache, while a long message produces 2,300–2,900 output tokens (including reasoning). Without the cache, a short correction costs about $0.0005. At Pro's Fair Use ceiling, one account sending 200 long messages costs about $0.70 per day. Audio output tokens are the most expensive per token, so TTS is generated only on request and cached in the page.

## Latency

How it is measured:

- **Text.** `POST /naturalize/` returns a `Server-Timing` header (`db`, `moderation`, `provider`, `total`; whole milliseconds, never text), visible in the browser's network panel. Each `UsageEvent` stores `duration_ms`, `provider_duration_ms` and `moderation_duration_ms`.
- **Live voice.** The browser marks each step with `performance.now()`: microphone ready (`mic_ms`), session request (`session_ms`), WebRTC connection (`connect_ms`), listening (`startup_ms`, counted from the press), first words (`first_word_ms`) and stop to final text (`finalise_ms`), plus whether the final event arrived. They travel with the finish request and are stored on the session's `AudioUsageEvent`. `window.CorectVoice.lastTimings` shows the last session; `localStorage.corectVoiceDebug = "1"` also logs them to the console. The realtime session endpoint returns `Server-Timing` too.
- **Reports.** `python manage.py latency_report --days 7` prints p50/p95/min/max from the ledgers, per operation and source language, and for live voice. The benchmark commands are described under [Tests](#latency-benchmarks).

What changed, and why it is faster:

- Romanian makes one generative call instead of two (and is moderated once).
- One shared OpenAI client with a 20-second keep-alive, instead of a new client and TLS handshake for every request.
- A static prompt with `prompt_cache_key`, so about 99 % of input tokens are served from the provider's cache; a bounded output budget; the quota check is one conditional `UPDATE` in the common case.
- Voice: `preconnect` when the learner reaches for the microphone; the session request overlapping the microphone request when permission is already granted; the WebRTC offer prepared while the session request is in flight; after Stop, 200 ms for trailing speech (was a fixed 800 ms) and the wait ends as soon as the final transcript arrives (was up to 4 seconds). The silence auto-stop stays at 3 seconds.

### Text results

`benchmark_text_latency --live --samples 10`, one warm-up call per row, moderation on, 15 September 2026, whole request time in ms (p50 / p95). "Before" is the previous code (a new client for each call, Romanian as two calls), 8 samples. `fresh` rebuilds the client for every call, as before; `idle` waits 25 seconds (longer than the 20-second keep-alive) before each call.

| Text | Before | Shared client | Fresh client | After 25 s idle |
| --- | --- | --- | --- | --- |
| English with a mistake | 3930 / 4780 | **2416 / 3454** | 2811 / 6010 | 2503 / 4098 |
| Natural English | 2380 / 4818 | **1222 / 1605** | 2169 / 3065 | 1345 / 2381 |
| Romanian (2 calls → 1) | 5142 / 6588 | **1366 / 2991** | 2070 / 3337 | 1740 / 3532 |
| Long English email (436 characters) | 12741 / 16010 | **10863 / 16206** | 12253 / 17221 | 12216 / 17018 |

With a shared client the moderation check takes about 200 ms (p50) instead of 820–870 ms with a fresh one, because it no longer opens a new TLS connection; the model call itself varies by ±10–20 % between runs. Long texts are dominated by generation time (about 1,200 output tokens), so they gain least.

### Language-quality eval latency

The same 236 cases (live `gpt-5.6-luna`, moderation on, 15 September 2026), on the previous two-action engine and on the single action: p50 3562 → 2334 ms and p95 6813 → 5664 ms per request; the four long texts, three runs each, p50 16727 → 9950 ms. Romanian no longer waits for a correction attempt before its translation.

### Live voice results

Chromium's fake microphone playing generated English and Romanian speech into the real page, real OpenAI calls, `VOICE_REALTIME_DELAY=low`, 15 September 2026. "Before" is the previous flow (fixed 800 ms trailing wait, up to 4 seconds for the final text), 10 English and 10 Romanian runs; every other row is 20 runs, except English at 500 ms (18).

| Run | Startup p50 / p95 | Session request p50 / p95 | First words after speech p50 / p95 | Stop → final text p50 / p95 | Last word kept |
| --- | --- | --- | --- | --- | --- |
| Before (EN + RO) | 3049 / 6186 ms | 1184 / 2530 ms | 1405 / 3406 ms | 1453 / 1616 ms | 19 / 20 |
| English, trailing 0 ms | 2362 / 4041 ms | 500 / 1193 ms | 1325 / 1640 ms | 671 / 734 ms | 19 / 20 |
| **English, 200 ms (default)** | 1939 / 2281 ms | 417 / 516 ms | 1359 / 1485 ms | 880 / 945 ms | 20 / 20 |
| English, 300 ms | 1926 / 2200 ms | 391 / 483 ms | 1311 / 1415 ms | 944 / 1032 ms | 20 / 20 |
| English, 500 ms | 2003 / 2419 ms | 418 / 755 ms | 1322 / 1414 ms | 1147 / 1266 ms | 18 / 18 |
| **Romanian, 200 ms (default)** | 1966 / 2151 ms | 401 / 434 ms | 1503 / 1742 ms | 884 / 1010 ms | 20 / 20 |

`VOICE_TRAILING_AUDIO_MS=200` is the smallest window that kept the last spoken word in every English and Romanian run; 0 ms lost it once (that sweep also included one cold first run). The session request is faster mainly because the server reuses its connection to OpenAI when minting the client secret. The first words still depend on the provider (about 1.3–1.5 seconds after speech starts). Other `VOICE_REALTIME_DELAY` values (`minimal`, `medium`) have not been compared yet, so `low` is unchanged. Synthetic speech is not a person: repeat the manual smoke test with a real microphone.

## Tests

Backend tests (PostgreSQL must be running):

```sh
python manage.py test apps --settings=config.test_settings --noinput
```

Plan quota tests cover:
- tier resolution;
- 5, 20 and 200 successes followed by a refusal;
- each refusal message and its link;
- English and Romanian, typed and spoken text, each counting one;
- every failure giving the use back;
- a duplicate token costing one;
- the last use going to exactly one of several simultaneous requests (threads on PostgreSQL);
- commits and releases racing without going negative;
- stale reservations;
- London calendar days in BST, GMT and on both clock-change days;
- a mid-day Pro upgrade;
- live transcription failing, the recording fallback and then submitting, which counts exactly one;
- ten speech plays counting nothing;
- plan-aware voice guardrails;
- history and profile access with the allowance used up;
- plan recorded at request time;
- the dashboard plan summary;
- the plan backfill migration;
- startup validation of impossible limits;
- the `core.W001` warning.

Tests use dummy configuration and mocked OpenAI calls; they make no paid calls. `python manage.py test` also selects test settings by default, unless `DJANGO_SETTINGS_MODULE` is explicitly set. `--noinput` lets Django recreate a test database left behind by an interrupted run; test settings disable persistent database connections so teardown is not blocked. The tests cover English and Romanian fixtures through the single action (exactly one provider call and one moderation call for each, with `store=False`, the schema, the prompt cache key and the output budget), the language registry (with a guard against language checks scattered outside it), the shared client (one instance across threads, rebuilt on settings changes, never closed by a request), schema validation, unchanged correct text, silent capitalisation and punctuation, natural versions, unsupported languages, line-break and spacing normalisation, the four result outcomes and old history rows (including English → Romanian), `Server-Timing`, refusal/truncation/timeout handling, CSRF, escaping, signup, username or email sign-in, profile username/password changes, header sign-in state, ownership, persistence, mistake category pages, private statistics and concurrent database limits.

Analytics tests (`apps/analytics/tests/`) cover usage events for registered and anonymous corrections and translations, failures, rejections, the effective operation and source language, `unclassified` requests and durations; real usage capture from provider responses (including rejected responses) with `store=False` intact; cost calculation, unknown pricing and cost recalculation; that anonymous text, response text and IP addresses are never persisted; visitor cookie creation, reuse and rotation; signup and sign-in conversion without duplicated events; staff-only access; period, audience, type and model filters; token and cost totals; constant query counts as report rows grow; the historical backfill and the effective-operation backfill; `latency_report` percentiles; and the read-only admin. `test_audio_analytics.py` covers audio pricing and its validation, grand total = text + audio and audio = speech to text + text to speech, shares, unknown audio pricing, user and visitor reports with audio at constant query counts, detail pages and the read-only audio ledger admin. Realtime analytics tests cover the split into realtime and file speech to text (grand total = text AI + realtime + file + TTS, audio = realtime + file + TTS, voice input = realtime + file), shares, provider-reported and server-observed durations, period and model filters, user and visitor report columns and sorting, detail pages, the admin transcription-mode filter and the migration marking earlier transcriptions as `file`.

Learning tests (`apps/learning/tests/`) cover pattern derivation and category checks; occurrences and patterns from corrections (British preferences excluded); new, recurring, improving, mastered and resurfaced statuses; priority order; spaced review (success later, failure sooner, due patterns); stored exercises reused without AI, one generated batch recorded once with its prompt version, AI failure falling back to editorial exercises, invalid batches; deterministic grading and AI only for unmatched open answers; today's plan and session completion; insight cards and the onboarding state; the minimal generation context; the dashboard, practice flow, ownership (another learner gets 404 and sees nothing); the correction hint and "Exersează acum"; learning AI in the staff analytics total without double counting and per user; and an idempotent `rebuild_learning_profiles` that never calls AI or edits corrections.

Live transcription tests (`apps/assistant/tests/test_voice_realtime.py`) cover: the session endpoint accepting only POST with CSRF; the short-lived secret returned and the server API key never in the response; the fixed `gpt-live-transcribe` transcription configuration (languages, prompt, delay, no turn detection) whatever the request body says; the kill switch; the shared speech-to-text rate limit (including the file endpoint); provider timeouts and failures; visitor tracking on and off; one ledger row per session however often finish is called; $0.017 per minute at 10, 30 and 60 seconds; unknown pricing; provider durations trusted only within the server-observed window; connect failures at $0; interrupted and closed sessions; tampered, foreign, expired and malformed finishes; registered users with no history entry and no stored speech, secret or IP; file transcriptions recorded as `file`; and cleanup of abandoned sessions without deleting ledger rows.

Voice tests (`apps/assistant/tests/test_voice_transcription.py`, `test_voice_speech.py`) cover WebM/MP4/WAV uploads, English and Romanian transcripts, the configured transcription model, language hints and prompt, no uploaded filename sent to the provider, no persisted audio, transcript, filename or IP, no history entry, missing/invalid/mismatched/ogg/oversized uploads, timeouts, empty and too-long transcripts, voice rate limits separate from text limits, CSRF, visitor tracking on and off, and the audio ledger having no content fields. Speech tests cover signed-token playback, refusal of raw text, tampered, foreign, expired and wrong-target tokens, the configured TTS model and voice, MP3 over server-sent events, `BRITISH_TTS_INSTRUCTIONS` always being sent and ruling out an American accent, missing usage events, provider failures, rate limits, linking to the correction and visitor, speaker controls only on English sentences (never Romanian text or explanations), and no provider call when results are rendered.

Optional browser tests (run them after, not in parallel with, the backend tests; both use the same test database):

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python manage.py test qa.browser_check --settings=config.test_settings --noinput
# one area only: python manage.py test qa.browser.test_voice --noinput
```

These run a temporary Django server against a PostgreSQL test database, mock only the AI and voice services (the voice test also mocks the microphone, `MediaRecorder` and audio playback in the browser), exercise real forms and HTMX, and write screenshots/layout measurements under ignored `artifacts/browser/`. They cover 360, 375, 390, 430, 768, 1024 and 1440px, the single action for English with mistakes, correct but unnatural English, natural English and Romanian (no leftover button text, no overflow), scrolling the result below the sticky header, errors, the mobile menu, accounts, history, practice, the `/learn/` dashboard at 1440, 1280, 1024, 390, 375 and 360px (sidebar, "Pentru tine azi" with the 7-day progress beside it, stats and priorities on desktop; one column with today first on phones), a full five-exercise session on a phone, a repeated correction leading through "Exersează acum" to a session (learning AI mocked), JavaScript-disabled operation, and voice: the microphone inside the text box with an accessible name, recording, stopping, transcript insertion at the caret and counter update, speaker controls on the correction and the natural version only when present, and on the British English made from Romanian, pause and replay from the page cache, and speakers working after an HTMX replacement (with live transcription switched off, to cover the fallback). Live transcription browser tests replace `RTCPeerConnection` and the OpenAI calls URL with stand-ins that play provider events, and cover: transcript state rules (a revised delta replaces instead of duplicating, committed items plus new partials, prefix and suffix around the caret, punctuation spacing, the character limit never cutting a word, item ordering); pressing the microphone, the listening state, a read-only text box and disabled buttons; words appearing progressively in the text box and the counter updating before Stop; the commit on Stop, the final transcript kept, speech after the stop ignored, editing restored, tracks stopped and one ledger row; „Vreau să sune natural!” afterwards; the microphone kept on during the trailing window, then muted and committed; the final event ending the wait at once, and the safety limit when it never comes; timings reported to and stored by the server without any text; the session request overlapping `getUserMedia` only when permission is already granted (no quota used when it is denied); the `preconnect` hint only on intent; spoken Romanian sent like typed Romanian; an interrupted connection keeping the text without file transcription; a connection failure falling back to recording on the same microphone stream; stopping at 2,000 characters; and the page-close beacon. They also cover the `/mistakes/` list (status beside the name, details on one line, the arrow into exercises), the menu order, the empty result card fitting one screen at 360×640, and an axe-core audit (WCAG 2 A/AA and 2.1 AA, no serious or critical findings) of the homepage, the four results and `/despre/`, loaded from `AXE_CORE_PATH` or `artifacts/axe.min.js` and skipped when neither exists. They require no API key and make no paid calls.

### Language-quality eval

A live, opt-in evaluation of the single action, separate from the unit fixtures. It is never part of CI: every command below refuses to run without `--live`, without `OPENAI_API_KEY`, or with the test settings' model, and writes nothing to the database.

```sh
python manage.py eval_naturalize --live
python manage.py eval_naturalize --live --group ro_to_en --repeat 3 --concurrency 4 --json-out artifacts/evals/ro.json
python manage.py eval_naturalize --live --group ro_intent_first --group polite --repeat 2
python manage.py eval_naturalize --live --reasoning-effort low --fail-under 0.95
```

`apps/assistant/evals/naturalize_cases.jsonl` holds 303 cases: word-for-word Romanian transfer in English (55), everyday UK contexts such as work, the NHS, school, a landlord or the council (35), tense and time (10), punctuation-only changes (10), already natural English (30), correct but unnatural English (22), valid American English (12), English with Romanian words (8), Romanian into British English (45: formal, casual, with and without diacritics, idioms), intent-first Romanian (`ro_intent_first`, 48: lifts and picking up, lateness, calls and messages, visits, invitations and refusals, work, the GP, school, a landlord, the council, shopping, Romanian idioms and transfer, casual and professional register, no diacritics, English words, and counterexamples where an idiom would invent context), unsupported languages (5), long texts (4) and Mod Politicos (19, 7 of them Romanian). Each case states what must hold (language, operation, whether there are mistakes, whether a natural version is required or forbidden, phrases the output must contain, with several acceptable alternatives, phrases it must not contain because they are wrong or Romanian-shaped, and `must_not_invent`: meaning the input does not state, reported separately as `invented`). Phrase checks match whole words and ignore case, punctuation, apostrophe style and a trailing 's. The report prints the pass rate overall and per group, routing accuracy, error precision and recall, the false-error and paraphrase rates, the number of cases with invented meaning, error codes, latency and token percentiles, the cached-input ratio, the largest output as a share of `max_output_tokens`, and every failing case; `--json-out` saves the full results under `artifacts/`.

### Latency benchmarks

```sh
python manage.py benchmark_text_latency --live --samples 10 --modes reused,fresh,idle
python manage.py benchmark_voice_latency --live --base-url http://127.0.0.1:8765 --runs 20 --languages en,ro --trailing-ms 0,200,300,500,800
python manage.py latency_report --days 7
```

`benchmark_text_latency` runs short and long English and Romanian texts through `NaturalizeService` and reports total, provider and moderation time (p50, p95, min, max) with the shared client (`reused`), a new client per request (`fresh`) and after an idle pause (`idle`). `benchmark_voice_latency` needs a running server with speech-to-text limits raised for the run; it generates British and Romanian speech once (cached in `artifacts/voice-bench/`), plays it into Chromium's fake microphone, presses the real microphone button, stops at a fixed moment and reads `window.CorectVoice.lastTimings` and the text box, for each trailing window. It reports microphone, session, connection and startup times, first words, stop-to-final time, how often the final event arrived and whether the last spoken word was kept. `latency_report` needs no API key: it prints percentiles from the usage ledgers per operation and source language, and for live voice sessions. The benchmarks make paid calls.

### Manual live smoke test

Run this with a real key before accepting a voice release. It makes a few paid calls and is not part of automated tests.

1. On <http://127.0.0.1:8000/> (or HTTPS), press the microphone and allow access.
2. Say slowly: "I would like to meet you tomorrow." **Confirm the words visibly appear in the text box before you press Stop.** A successful connection alone does not pass this test.
3. Press the microphone again and check the final sentence.
4. Edit the text, then press „Vreau să sune natural!” and confirm the result: English with a mistake shows "Engleza ta, corectată"; a correct but stiff sentence ("I want to ask you if you can help me with a thing.") shows "Sună mai natural:"; a natural sentence shows "✓ Sună deja natural."
4a. Type a Romanian sentence (with and without diacritics) and confirm "În engleză britanică" with natural English, then say one into the microphone and send it the same way.
5. Press the speaker beside the correction. Listen carefully: the pronunciation must be clear, natural contemporary British English. If "Sună mai natural:" appears, check its speaker too, and the speaker on a Romanian result.
6. Speak a Romanian sentence and confirm the Romanian text, with diacritics, appears while you speak. Then try a sentence mixing Romanian and English.
7. Open `/admin/analytics/` and confirm one realtime speech-to-text session per microphone use, its duration and a cost of $0.017 per minute, and that the audio total and all-sources total increased by the same amount.
8. Repeat steps 1–3 on a real iPhone (Safari) and Android phone (Chrome) over HTTPS.

Listen explicitly for American pronunciation (for example rhotic "r" in "car" or "water", flapped "t" in "better", or American vowels in "can't" and "bath"). **If the speech sounds American, voice output is not complete, even though the endpoint returned audio.** British accent quality is a product acceptance criterion.

## Production operation

Deploy the application behind HTTPS with `DJANGO_DEBUG=false`, a strong unique secret, the actual allowed hosts, a private PostgreSQL connection and a production database password. The application enables secure cookies (including the visitor cookie), HTTPS redirects, HSTS, clickjacking protection and normal Django CSRF/escaping protections. The microphone requires HTTPS in production.

```sh
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
waitress-serve --listen=127.0.0.1:8000 config.wsgi:application
```

WhiteNoise serves versioned compressed assets. Configure TLS at a trusted reverse proxy and ensure the WSGI URL scheme is correct (for Waitress, use its trusted-proxy settings, scoped to your proxy). Do not indiscriminately trust `X-Forwarded-Proto`. Match proxy request timeouts to the AI timeout (at least 60 seconds by default; every submission makes at most one provider call) and allow request bodies of at least `VOICE_MAX_BYTES` for `/assistant/transcribe/`. Live transcription audio travels over WebRTC directly between the browser and OpenAI, not through the proxy; if you add a Content-Security-Policy, allow `connect-src https://api.openai.com`. Run `cleanup_assistant` daily so abandoned live sessions are accounted for. `VOICE_REALTIME_ENABLED=false` (then restart) switches every browser to finished-recording transcription. Database backup/restore, HTTPS, secret rotation, scheduled cleanup and infrastructure monitoring are deployment responsibilities. See [Monitoring](#monitoring) for health checks, logs, alerts and error tracking; do not enable verbose OpenAI/HTTP logging in production. Keep `OPENAI_PRICING` and `OPENAI_AUDIO_PRICING` in step with the provider's published prices. Restrict staff status to people who may see usage and cost data.

### SEO

- **Indexable pages** (allow-list in `apps/core/seo.py`): `/`, `/about/`, `/confidentialitate/`, `/termeni/` and `/contact/` (when enabled). They get a `<link rel="canonical">` built from `SITE_URL` plus the path (query strings dropped), Open Graph (`og:site_name`, `og:url`, `og:image` 1200×630, `og:locale`) and Twitter `summary_large_image` tags, and a page-specific description (`{% block meta_description %}`).
- **Everything else is `noindex, nofollow`**, both as `X-Robots-Tag` (every response, including redirects, errors, JSON endpoints, health checks and admin) and as a robots meta tag on HTML pages: accounts and authentication pages, history, mistakes, progress, practice and learning, cookie settings, staff analytics. Private pages are also behind login; `robots.txt` is only a crawl hint and disallows `/admin/`, `/naturalize/`, `/assistant/` and `/analytics/`.
- **`/robots.txt` and `/sitemap.xml`** are generated (no extra app); the sitemap lists only the indexable pages, as absolute `SITE_URL` URLs, and never anything personal.
- **Structured data**: the homepage has JSON-LD `WebSite` and `Organization` (name, URL, logo, contact email when set) — no prices, ratings or reviews.
- **Share image**: `static/img/og-image.jpg`, rendered from the hero picture and logo by `python -m qa.tools.make_og_image`; rerun it when those change.

### Account emails

Every account needs one valid email address. It is stored trimmed and lower-case, so `Daniel@example.com` and `daniel@example.com` are the same address, and PostgreSQL enforces uniqueness with the partial index `accounts_user_email_ci_unique` on `LOWER(email)` (migration `accounts.0003`). Signup, the profile page and the admin apply the same rules; signing in with an email ignores case, and signing in with the username still works. Addresses are not verified (no email is sent).

The change is made in two stages so a deployment cannot fail half-way:

1. **Before deploying**, run `python manage.py report_email_integrity` against the production database (it only reads). It lists addresses shared by several accounts (masked, with the account IDs) and counts accounts without an address, and exits 1 if either exists.
2. **Shared addresses block `migrate`**: migration `accounts.0003` checks first and stops, changing nothing, if any address is shared. Decide per case which account keeps the address — usually the one that signs in; ask the owners if unsure — and give the others a different address the owner confirms, or delete a clearly abandoned duplicate. Never invent addresses. Then run `migrate` again: it normalises the existing addresses and creates the unique index. `auth_user` is small, so the index builds in moments; on a very large table, create it first without locking writes (`CREATE UNIQUE INDEX CONCURRENTLY accounts_user_email_ci_unique ON auth_user (LOWER(email)) WHERE email <> ''`, after normalising addresses) and the migration keeps it.
3. **Accounts without an address** keep working for public pages, but after signing in they are sent to their profile (`?email_required=1`) until they add one; AI requests are refused meanwhile (`email_required`). `check_production_health` reports how many are left.
4. **Stage 2** (later): once the report shows no account without an address, add a `CHECK (email <> '')` constraint in a new migration so the database also forbids blanks.

### Monitoring

Nothing here depends on one vendor: health checks are plain HTTP, logs go to standard output, alerts are a command's exit code, and Sentry is optional.

| What | How it is detected |
| --- | --- |
| Site down | An uptime monitor (the host's, UptimeRobot, Better Stack…) polls `GET /healthz` (process alive, no database access) every minute |
| PostgreSQL down | `GET /readyz` returns 503 `{"status": "unavailable", "checks": {"database": "error"}}`; use it for the load balancer's readiness check and a second uptime check. Errors are logged with `category=database` |
| Django errors / HTTP 500 | `django.request` logs each 500 with its stack (JSON field `exc_type`, never the exception message); with `SENTRY_DSN`, Sentry groups them and alerts |
| OpenAI unavailable | Every failed call is logged as `assistant_failed` / `voice_failed` / `learning_ai_failed` with `category=provider` and a fixed code; `check_production_health` alerts when provider failures pass `ALERT_ERROR_RATE_PERCENT` in the last hour |
| Quota or rate-limit pressure | Refusals are logged at INFO with `category=quota`; the staff dashboard shows quota hit rates per plan |
| Unusual AI cost | `check_production_health` compares today's and the last hour's spend from the three usage ledgers with `AI_COST_ALERT_DAILY_USD` / `AI_COST_ALERT_HOURLY_USD`, and flags calls without a price; the staff dashboard shows the same warning |

Health endpoints are exempt from the HTTPS redirect so the host can check them over plain HTTP; the request's `Host` must still be in `DJANGO_ALLOWED_HOSTS`. They return only `ok`/`error`.

**Logs.** With `LOG_FORMAT=json` every line is one JSON object: `time`, `level`, `logger`, `message`, `event`, `category` (`application`, `database`, `provider`, `quota`, `guardrail`), `request_id` (also returned to the browser as `X-Request-ID`, so a user's report can be matched to a log line), `method`, `path` (never the query string) and, for 4xx/5xx, `status`. Logs never contain submitted text, corrections, transcripts, audio, prompts, provider payloads, API keys, passwords, emails or IP addresses; exception messages are left out because database and provider errors can quote submitted values. Ship standard output to the host's log service and alert on `level=ERROR`.

**Alerts.** `python manage.py check_production_health` prints one JSON line and exits 1 when the database is unreachable, spend passes a threshold, provider failures pass the error rate, or calls have no price; it also logs `monitoring_alert` at ERROR (so Sentry, when enabled, sends an alert). Run it from cron or the host's scheduler:

```sh
# Every 15 minutes; cron emails the JSON line only when there is a problem (MAILTO=ops@example.com).
*/15 * * * * cd /srv/corect && .venv/bin/python manage.py check_production_health --quiet
# Or forward alerts to a webhook (Slack, Discord, a pager):
*/15 * * * * cd /srv/corect && .venv/bin/python manage.py check_production_health --quiet > /tmp/corect-health.json || curl -fsS -X POST -H 'Content-Type: application/json' --data @/tmp/corect-health.json "$ALERT_WEBHOOK_URL"
```

**Sentry.** Set `SENTRY_DSN` (and optionally `SENTRY_ENVIRONMENT=production`, `SENTRY_RELEASE=<git sha>`) and restart. Unhandled exceptions and ERROR logs become events tagged with their `category`. Personal data is not sent: no default PII, no request bodies, cookies, headers or query strings, no local variables, no breadcrumbs, no exception messages, and the user is reduced to its numeric ID. Without a DSN the SDK is never initialised.

### Deployment behind a proxy

Behind nginx, a load balancer, the host's proxy or Cloudflare, `REMOTE_ADDR` is the proxy, not the visitor. Without configuration every anonymous visitor would share one daily quota. Configure exactly the proxies you run, never "any address":

| Set-up | Configuration |
| --- | --- |
| No proxy (Waitress faces the internet) | Nothing: `CLIENT_IP_HEADER=none` is the default |
| nginx or a load balancer on the same host or private network | nginx: `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` — Django: `CLIENT_IP_HEADER=x-forwarded-for`, `TRUSTED_PROXY_CIDRS=127.0.0.1,::1` (or the balancer's private network, e.g. `10.0.0.0/24`) |
| nginx that already resolved the address (`real_ip` module) | nginx: `proxy_set_header X-Real-IP $remote_addr;` — Django: `CLIENT_IP_HEADER=x-real-ip`, `TRUSTED_PROXY_CIDRS=127.0.0.1` |
| Cloudflare straight to Waitress | `CLIENT_IP_HEADER=cf-connecting-ip`, `TRUSTED_PROXY_CIDRS` = the ranges published at https://www.cloudflare.com/ips/ (review them when Cloudflare changes them); block direct access to the origin |
| Cloudflare → nginx → Waitress | Let nginx restore the visitor address from Cloudflare (`set_real_ip_from` Cloudflare ranges, `real_ip_header CF-Connecting-IP`) and pass it as `X-Real-IP`; Django trusts only nginx: `CLIENT_IP_HEADER=x-real-ip`, `TRUSTED_PROXY_CIDRS=127.0.0.1` |

For `x-forwarded-for` the list is read right to left, skipping trusted proxies, so an address a visitor typed into the header is never used. If you instead use Waitress's own `--trusted-proxy` options (which rewrite `REMOTE_ADDR`), leave `CLIENT_IP_HEADER=none` so the address is not resolved twice. Check the result after deploying: two devices on different networks must get separate anonymous allowances, and `curl -H "X-Forwarded-For: 1.2.3.4"` from outside must not reset one.

The V1 settings are intentionally fixed. Live voice input (transcription) and British voice output are included; a realtime voice conversation or speech-to-speech assistant, realtime AI correction, social login, password-reset email delivery, public deployment and AI-generated practice are not. Saved activity counts are not an English proficiency score.

## Dependency updates

- **Dependabot** (`.github/dependabot.yml`) opens pull requests every Monday for Python packages (`requirements*.txt`) and GitHub Actions. Patch and minor updates are grouped into one PR per ecosystem; each major version gets its own PR, to be read against the package's changelog before merging.
- **CI** (`.github/workflows/tests.yml`) runs on every push and pull request against PostgreSQL 16: `manage.py check`, `makemigrations --check`, the full backend suite, and `pip-audit` for known vulnerabilities in the pinned requirements. The Playwright browser checks run on demand (Actions → tests → Run workflow → "browser"), with screenshots uploaded as an artifact.
- **Nothing merges automatically.** Merge a dependency PR only when `tests` is green; for Django, OpenAI, psycopg or Playwright updates also run the browser checks and a manual smoke test. Update the pinned version in `requirements.txt` in the same PR if Dependabot did not.

Owner actions in the GitHub repository settings: protect `main` and require the `backend` and `audit` checks before merging; enable Dependabot alerts, Dependabot security updates and secret scanning (Settings → Code security).

## Visual reference

The supplied mobile and desktop images guide the homepage hierarchy, blue/white palette, rounded editor/results, paired actions and navigation. The implementation uses actual responsive HTML, not a phone/browser frame. The editor begins empty and uses the specification's 2,000-character default. Long responses scroll naturally.

Front-end libraries are vendored in `static/vendor/`, so no runtime CDN or frontend build tool is required: HTMX 2.0.8 with its licence file, and Chart.js 4.5.0 (UMD build, MIT licence banner at the top of the file), loaded on the progress page and the staff analytics pages. Voice uses only native browser APIs, including WebRTC for live transcription (`static/js/voice.js`, `static/js/transcript-state.js`, `static/css/voice.css`).

## Implementation verification

Verified locally on 15 September 2026 after plan quotas were added (anonymous 5, Free 20, Pro 200 a day under Fair Use):

- 309 backend tests and 30 browser workflow tests passed. The browser tests cover:
  - anonymous visitors using up the day's allowance and getting the account invitation, with speech already in the page still playing;
  - Free users getting the Pro message and link, and Pro users the Fair Use message with no link;
  - the profile's plan line;
  - history, progress and mistakes still opening with the allowance used up;
  - a spoken transcript costing nothing until it is submitted, then exactly one use.

  axe-core 4.11 found no WCAG 2 A/AA issues on the anonymous quota alert, and the other audited pages stayed clean.
- `makemigrations --check` found nothing missing. `assistant.0004_naturalize_usage` and `analytics.0009_plan_tier` applied to the development database, reversed and re-applied cleanly.
- `manage.py check` reports `core.W001` for the old `RATE_LIMIT_MINUTE`/`RATE_LIMIT_DAY` variables still set in a local `.env`; startup is unaffected.

Verified locally on 15 September 2026 after the single action („Vreau să sune natural!”) replaced Corectare and Traducere:

- 278 backend tests and 26 browser workflow tests passed, including axe-core 4.11 on the homepage (390 and 1440px), the English, correct-but-unnatural and Romanian results, and `/despre/` (no serious or critical WCAG 2 A/AA findings after darkening the secondary button's hover text, which was 4.42:1).
- `makemigrations --check` found no missing migrations; `assistant.0003`, `analytics.0007` and `analytics.0008` applied to the development database.
- Live eval, 236 cases (`gpt-5.6-luna`, moderation on): the single action passed 229 (routing 100 %, error precision 1.0, recall 0.978), the previous two-action engine 232. Its two weaker groups were repeated three times on both: correct but unnatural English 64/66 on both; Romanian transfer in English 157/165 against 154/165; false errors 10.6 % against 19.7 %. The remaining failures are reviewed cases where either representation is defensible ("at what hour" kept as correct with a natural "what time"; a stiff sentence corrected directly instead of given a natural version) plus two recurring misses shared with the old engine ("actual price", "because I move"). The previous engine was then removed.
- Output budget: the first run showed long English with many mistakes using 88 % of `max_output_tokens`; after raising the budget, the long group (three runs) peaked at 61 % with no incomplete responses.
- Voice latency and the 200 ms trailing default: see [Live voice results](#live-voice-results). Speech came from generated audio, not a person; the real-microphone smoke test is still to do.

Verified locally on 13 September 2026 with Python 3.11, Django 5.2.17 and PostgreSQL 16:

- 138 backend tests (including 17 live transcription tests and 4 realtime cost analytics tests) and 9 browser workflow tests passed.
- `makemigrations --check` found no missing migrations; `migrate` applied `analytics.0001`–`0003`, backfilling one ledger row per existing saved request (35 of 35, original timestamps, no tokens or cost), then `analytics.0005_audio_stt_mode` and `assistant.0002_realtime_transcription_session`.
- Django system checks and `check --deploy` with production settings passed.
- Live OpenAI verification is complete for text: the application has been tested successfully with `OPENAI_MODEL=gpt-5.6-luna`, including multi-line input, trailing whitespace, tense/time contradictions, silent capitalisation and punctuation, native versions (prepositions, American vocabulary, unnatural but correct text), Romanian sent to Correct, translation and a long message. These are spot checks, not a systematic linguistic evaluation.
- Live voice service check: `gpt-4o-mini-tts` with voice `cedar` returned MP3 over server-sent events (146 `speech.audio.delta` events and one `speech.audio.done` with usage: 84 input and 101 audio tokens, $0.0012624). `gpt-transcribe` transcribed that MP3 exactly and a Romanian sample with correct diacritics, reporting duration usage (4.00 s, $0.0003; 6.00 s, $0.00045). **The British accent has not yet been validated by a human listener**; complete the manual live smoke test above before accepting voice output.
- Live transcription check (13 September 2026, real OpenAI calls through the running application in Chromium at 390px): the session endpoint minted a transcription client secret with `gpt-live-transcribe`, `languages` `en`/`ro` and no turn detection, and the browser connected over WebRTC (`/v1/realtime/calls`, 201). The microphone was Chromium's fake capture device playing pre-generated speech, not a person. Words appeared in the text box while the audio was still playing, before Stop: "I would like to meet you tomorrow at the station, if that works for you." built up over 14 updates, the first about 1.5 seconds after speech began; the Romanian sentence appeared over 10 updates with correct diacritics. The listening state was reached about 3.4 seconds after pressing the microphone. After Stop the text stayed editable, and Corectare on the edited text returned the normal correction. Each session produced exactly one ledger row (`stt_mode = realtime`, 10.00 seconds, `metering_source = provider`, $0.00283333 = 10 s ÷ 60 × $0.017); the realtime, audio and all-sources totals rose by the same $0.00849999. The mixed Romanian/English sample, read by an English TTS voice, came back with the Romanian words misheard, so mixed-language accuracy still needs a real speaker. **Speaking into a real microphone (desktop, iPhone Safari, Android Chrome) remains part of the manual live smoke test.**
- Live analytics smoke test on the development server: two anonymous corrections (one Romanian, auto-translated with both provider calls summed), signup from the same browser (visitor converted via signup), then a registered correction linked to its saved history. Events recorded real `gpt-5.6-luna` token usage and matching cost estimates; all five staff analytics pages returned 200 with no page errors; the test accounts and events were removed afterwards.
- Screenshots reviewed for the homepage (desktop and mobile), sticky header and editor, the microphone inside the text box at 1440, 390 and 375px (44×44px, clear of the counter), live transcription at 390px (words in the text box, active microphone, disabled buttons, counter updating), mobile drawer menu, history, mistake category pages, progress chart, profile and the analytics cost breakdown; no horizontal overflow. The mobile homepage fits without scrolling at 360×740, 375×667, 390×844 and 768×1024.
- Known issue: one long-message correction failed once with `invalid_or_failed_response` (the provider response did not pass validation); it did not recur in later runs.
- Axe-core 4.11 (no WCAG 2 A/AA or WCAG 2.1 AA violations on the homepage, signup, sign-in and the former settings page at 390px and 1440px) predates the September interface changes and has not been re-run. Automated checks are not a complete accessibility certification.

Local screenshots and audit reports are under ignored `artifacts/`; rerun the browser command above to regenerate workflow screenshots.
