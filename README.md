# Corect.uk

A lightweight British English assistant for Romanian speakers. Django templates with locally served HTMX and Chart.js provide an accessible, responsive interface; OpenAI runs exclusively on the server. Learners can type or speak their text, and listen to corrections read aloud in a British accent. Staff get a usage analytics area covering registered users, anonymous visitors, real token and audio usage, and estimated AI cost by source.

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

Set `OPENAI_API_KEY` to your API key and `OPENAI_MODEL` to a Responses API model that supports Structured Outputs (the project runs with `gpt-5.6-luna`). Both are required for live correction/translation; the application still starts and displays a friendly unavailable message without them. Voice uses the same key with the defaults `gpt-transcribe` and `gpt-4o-mini-tts`. There is no fake production AI fallback.

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

`migrate` also runs the analytics backfill (each existing saved request gets one usage ledger row, see [Historical data](#historical-data)) and creates the audio usage ledger (`analytics.0003_audiousageevent`). There is no historical audio usage, so nothing audio-related is backfilled. The backfill can be reversed with `python manage.py migrate analytics 0001`.

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
| `DATABASE_NAME`, `DATABASE_USER` | Default `englishcoach` |
| `DATABASE_PASSWORD` | Required database password |
| `DATABASE_HOST`, `DATABASE_PORT` | Default `127.0.0.1`, `5434` |
| `OPENAI_API_KEY` | Server-only credential, used for text and voice |
| `OPENAI_MODEL` | Explicit text model selection, no hidden default |
| `OPENAI_TIMEOUT` | Provider timeout for text and voice, default 60 seconds (long messages can take 20–25 seconds) |
| `OPENAI_PRICING` | Optional JSON text price table; empty uses the built-in `gpt-5.6-luna` prices, `{}` disables text cost estimates (see [Token tracking and pricing](#token-tracking-and-pricing)) |
| `OPENAI_TRANSCRIBE_MODEL` | Speech-to-text model, default `gpt-transcribe` |
| `OPENAI_TTS_MODEL` | Text-to-speech model, default `gpt-4o-mini-tts` |
| `OPENAI_TTS_VOICE` | Voice for British speech, default `cedar` |
| `OPENAI_TTS_SPEED` | Speech speed from 0.25 to 4.0, default `1.0` (invalid values stop the app at startup) |
| `OPENAI_AUDIO_PRICING` | Optional JSON audio price table; empty uses the built-in prices, `{}` leaves audio costs unknown (see [Audio pricing](#audio-pricing)) |
| `VOICE_MAX_SECONDS` | Recording limit in the browser, default 60 |
| `VOICE_MAX_BYTES` | Upload limit for one recording, default 5,000,000 bytes (the provider allows 25 MB) |
| `VOICE_TRANSCRIBE_LANGUAGES` | Language hints sent to transcription, default `en,ro`; empty sends none. Mixed-language speech still works |
| `VOICE_TRANSCRIBE_LIMIT_MINUTE`, `VOICE_TRANSCRIBE_LIMIT_DAY` | Transcriptions per actor, default 5/minute and 50/day |
| `VOICE_TTS_LIMIT_MINUTE`, `VOICE_TTS_LIMIT_DAY` | Speech generations per actor, default 10/minute and 100/day |
| `VOICE_SPEECH_TOKEN_MAX_AGE` | Lifetime of a result's signed speech token in seconds, default 2700 (45 minutes) |
| `ANALYTICS_VISITOR_COOKIE` | `true` by default; `false` stops setting the anonymous visitor cookie |
| `ASSISTANT_MAX_CHARACTERS` | Maximum input length, default 2000 |
| `RATE_LIMIT_MINUTE`, `RATE_LIMIT_DAY` | Correct/Translate requests, default 10/minute and 100/day per actor |

An actor is a signed-in user or an HMAC of an anonymous visitor's IP address. Forwarded headers are not trusted. If deploying behind a reverse proxy, configure the application server to establish the real `REMOTE_ADDR` only from that trusted proxy; otherwise all anonymous visitors share the proxy's quota. Never accept arbitrary client forwarding headers. This rate-limiting identity is separate from the analytics visitor ID described below. Voice calls count in their own namespaced counters (`voice-stt:` and `voice-tts:`), so they never use up Correct/Translate quotas.

## Architecture and behaviour

- `core`: homepage, shared layout and settings. The public interface is in Romanian (`LANGUAGE_CODE = "ro"`, so Django's own form labels, validation messages and dates are Romanian too); `apps/core/middleware.py` keeps `/admin/` and the staff analytics in English. Learning content stays in English: corrections, native versions, translations into English and practice questions. The main buttons are "Corectare" and "Traducere". The header (logo, menu, user icon) stays sticky on every screen size. Desktop shows every page in the top menu; mobile uses a hamburger that opens a slide-in drawer with an icon per page. The user icon links to sign-in when signed out and to the profile when signed in (the profile is not repeated in the menus). On desktop the blue editor panel stays sticky beside a long result; on mobile the homepage fits one screen without scrolling, and "Corectură nouă" (plus "Autentificare / Creează cont" when signed out) appears below a correction or a translation. The text box contains the microphone button.
- `accounts`: signup, sign-in with a username or an email address (`EmailBackend`, only when the email belongs to exactly one account), sign-out, and a profile page to change username, email and password (the session stays signed in after a password change).
- `assistant`: Pydantic schemas, versioned prompts, independent string-based correction/translation services, OpenAI adapter (returning the parsed result together with provider usage), voice services and endpoints (`services/voice.py`, `voice_views.py`), pricing, persistence and database-backed limits.
- `learning`: private history, per-category mistake pages, repeated patterns, a 30-day progress chart and a curated practice bank. Each category in "Top mistake categories" links to `/mistakes/<category>/`, which lists the user's own mistakes with links to the full correction and to that category's exercise (`/practice/?category=<category>`). The progress chart (Chart.js, "Trend" and "By day" views) keeps a daily-counts table as its accessible fallback. Practice answers are checked on the server; no AI calls are made for practice.
- `analytics`: the append-only text usage ledger (`UsageEvent`), the append-only audio usage ledger (`AudioUsageEvent`), anonymous visitors (`AnonymousVisitor`), the staff dashboard under `/admin/analytics/`, read-only admin registrations and the `recalculate_usage_costs` command.

`POST /assistant/correct/` and `POST /assistant/translate/` accept `text`, `submission_token` (UUID), and Django's CSRF token. Normal submissions return the homepage; `HX-Request: true` returns result HTML plus out-of-band updates for a fresh submission token and the actions below the result. Error codes are 400 (validation), 409 (duplicate), 422 (language), 429 (quota) and 503 (service failure). GET never calls AI.

Each accepted action makes one `responses.parse` call with a Pydantic schema, except Romanian text sent to Correct, which makes a second (translation) call within the same accepted submission. Automatic SDK retries are disabled; requests are stateless and `store=False` is sent to OpenAI. No history is sent as context. The provider's own data policies still apply; `store=False` is not a promise of zero provider retention.

Correction behaviour:

- **Minimal correction.** `corrected_text` fixes only genuine errors, so the learner sees exactly what was wrong. When the model reports no genuine errors, correct English is restored unchanged (apart from silent capitalisation and punctuation fixes).
- **Silent capitalisation and punctuation.** Capital letters, full stops, commas and similar marks are fixed in the corrected and native text but never listed as corrections, mentioned in explanations, highlighted in the sentence comparison or stored as mistakes.
- **Tense versus time expression.** A verb tense that contradicts a time expression ("I was there tomorrow") is always corrected, keeping the time expression; the explanation mentions the alternative of changing the time word.
- **Native version.** An optional `native_text` shows how a British English speaker would naturally phrase the text (word order, prepositions, collocations, British vocabulary such as "shop" for "store"), with a short Romanian note. It is shown only when it differs from the correction by more than capitals and punctuation, including for text that is grammatically correct but unnatural.
- **British preferences.** British spelling suggestions remain separate, do not alter otherwise correct American English in the corrected text, and never count as mistakes.
- **Romanian sent to Correct** is translated automatically, with a short note, instead of asking the learner to resubmit.
- **Input normalisation.** Browser line breaks (CRLF) become LF and outer whitespace is trimmed before the provider call. The model's echo of the original is compared by words, so spacing differences never fail a request.

Translation chooses direction from the predominant language. For text with genuine errors, linguistic quality still depends on the configured model and prompt; schema validation cannot prove grammar quality.

Only authenticated successful requests save text/results. Grammar corrections are saved atomically with their parent request. Anonymous text/results are not written to the database. Authenticated failures store a sanitised code and metadata without text. User deletion cascades to saved requests. History and statistics always filter on the current authenticated user.

Atomic PostgreSQL counters prevent parallel requests from exceeding a quota. A unique actor/submission token prevents duplicate provider calls across workers. Claims are retained for seven days; expired counters and older claims should be cleaned daily:

```sh
python manage.py cleanup_assistant
```

The cleanup command does not touch the usage ledgers or visitors.

Prompts are versioned in `apps/assistant/services/prompts.py` (currently `2026-09-v5`); the version is saved with every request and usage event. Changing them requires reviewing the language examples. Future speech-to-text can pass its string directly to `CorrectionService.correct(text)` or `TranslationService.translate(text)`.

## Voice

Voice has two parts: speaking text into the text box, and listening to the correction in British English. **This is not a realtime voice conversation**: a recording is transcribed only after the learner stops it, and speech is generated only when a speaker button is pressed. No browser `SpeechRecognition` or `SpeechSynthesis` is used; all voice work goes through the server and OpenAI.

### Voice input (speech to text)

1. The learner presses the microphone inside the text box. The browser asks for microphone permission (`getUserMedia`) and records with `MediaRecorder`, choosing the first supported format from `audio/webm;codecs=opus`, `audio/webm`, `audio/mp4`, `audio/wav`.
2. Pressing the microphone again stops the recording; it also stops automatically after `VOICE_MAX_SECONDS`. Every microphone track is stopped as soon as recording ends.
3. The recording is uploaded to `POST /assistant/transcribe/` (CSRF protected) and sent to `OPENAI_TRANSCRIBE_MODEL` with English and Romanian hints (`languages`) and a prompt saying the speaker may mix both. One language is never forced, so English, Romanian and mixed speech all work.
4. The transcript is inserted at the caret, with sensible spacing, and the character counter updates. Nothing is corrected or translated automatically: the learner checks the text, then chooses Correct or Translate. If the result would exceed `ASSISTANT_MAX_CHARACTERS`, nothing is inserted and a message explains why.

The server identifies the container from the file's first bytes (WebM, MP4/M4A, WAV or MP3) and requires the declared content type to match; the content type alone is never trusted. Ogg recordings are not accepted because OpenAI does not document them for transcription. Oversized bodies are refused before they are read, and the upload is held in memory only (never a temporary file) up to `VOICE_MAX_BYTES`. Errors return a short message: permission denied, unsupported browser, recording too long, "We couldn't understand that recording", or voice temporarily unavailable. The microphone button announces its state (record, stop recording, transcribing) through `aria-pressed`, its label and a live region.

### Voice output (British text to speech)

The corrected sentence, the unchanged sentence of a correct text, the Native version and a translation into English each have a speaker button ("Ascultă corectura în engleză britanică", "Ascultă versiunea nativă în engleză britanică", "Ascultă traducerea în engleză britanică"). Romanian explanations, translations into Romanian and anything else never get one. Nothing plays automatically.

Pressing a speaker sends its token to `POST /assistant/speech/`, which calls `OPENAI_TTS_MODEL` with the configured `OPENAI_TTS_VOICE` and `OPENAI_TTS_SPEED`, MP3 output and server-sent events, and always these instructions (`BRITISH_TTS_INSTRUCTIONS` in `apps/assistant/services/voice.py`, the only copy):

> Speak in natural contemporary British English with a neutral Southern British accent. Use British pronunciation, stress and intonation. Sound warm, clear and conversational. Do not use an American accent. Do not exaggerate Received Pronunciation. Speak at a normal, learner-friendly conversational pace without sounding slow, theatrical or robotic. Read exactly the supplied English sentence and add no commentary.

Only one sentence plays at a time; pressing another speaker stops the current one, and pressing a playing speaker pauses it. The returned audio is cached in browser memory for the page, so replaying the same sentence makes no new paid request. Speaker buttons use event delegation and keep working after HTMX replaces the result.

**No open text-to-speech proxy.** When a result is rendered, each speakable sentence gets a Django-signed token (`signing.dumps`, salt `corect.speech.v1`) containing the approved English text, the target (`correction`, `native` or `translation`) and, for a live correction, its usage event ID. The speech endpoint accepts only that token, verifies the signature and its age (`VOICE_SPEECH_TOKEN_MAX_AGE`), and never reads a raw `text` parameter. Tampered, foreign, expired or wrong-target tokens are refused before any provider call. Rendering a result never calls text to speech: 100 corrections with no Listen clicks cost nothing for audio.

### Voice privacy

- Raw audio, uploaded filenames, transcripts, speech text, generated audio and provider payloads are never stored or logged, for anonymous and registered users alike. Audio exists only in request memory while it is sent to the provider; generated MP3 is returned directly with `Cache-Control: no-store`.
- Transcription never adds anything to history. A registered user's text is saved only when they actually press Correct or Translate, exactly as before.
- Logs contain sanitised codes only (`voice_failed code=... operation=...`), never token contents or exception text.
- Anonymous voice usage is attributed to the same `AnonymousVisitor` cookie as text usage (created on first use if needed), or recorded without a visitor when `ANALYTICS_VISITOR_COOKIE=false`. No new identity system is added.

Sanitised audio error codes include `microphone_upload_invalid`, `audio_too_large`, `unsupported_audio`, `transcription_timeout`, `transcription_failed`, `transcription_empty`, `transcript_too_long`, `speech_token_invalid`, `speech_token_expired`, `tts_timeout`, `tts_failed`, `audio_rate_limit` and `voice_not_configured`.

## Usage analytics (staff)

Open <http://127.0.0.1:8000/admin/analytics/> (in production: `https://<your-host>/admin/analytics/`). Only authenticated staff or superuser accounts can view it; everyone else is redirected to the admin sign-in. Make an account staff with `python manage.py createsuperuser` or by ticking "Staff status" on the user in `/admin/`.

| Page | What it shows |
| --- | --- |
| `/admin/analytics/` | AI cost by source (all sources, text AI, audio, voice input, voice output, each with its share), registered users, anonymous visitors, signup conversion, text requests (period, today, 7 and 30 days), successes, failures, rejections, corrections, translations, voice transcriptions, British TTS plays, text tokens, text activity chart, text errors, text models, audio usage, audio models, audio errors, top users and top visitors |
| `/admin/analytics/users/` | Every registered user with joined date, last activity, text requests, corrections, translations, successes, failures, text tokens, voice transcriptions, TTS plays, text AI cost, voice input cost, voice output cost, audio cost and total AI cost; sortable, searchable by username or email, paginated |
| `/admin/analytics/users/<id>/` | One user's cost by source, text usage by period, audio usage, models, errors, daily activity and linked anonymous visitors |
| `/admin/analytics/visitors/` | Anonymous visitors (`anon-7e5238`) with text or voice activity: first/last seen, active days, text requests, voice transcriptions, TTS plays, costs by source and conversion; searchable by short or full ID |
| `/admin/analytics/visitors/<uuid>/` | One visitor's cost by source, anonymous text and voice usage, requests before and after conversion, errors and daily activity; never text or audio |

Filters: period (today, 7, 30, 90 days, all time), audience (all, registered, anonymous), request type (Correct, Translate; applies to text figures only) and model (text and audio models, shown when more than one has been used). “—” means not recorded: pricing unavailable, or history saved before token tracking.

### Where the AI cost comes from

| Figure | Source |
| --- | --- |
| Text AI cost | Sum of `UsageEvent.estimated_cost` (Correct/Translate via `OPENAI_MODEL`) |
| Voice input (speech to text) | Sum of `AudioUsageEvent.estimated_cost` where `operation = transcription` (`OPENAI_TRANSCRIBE_MODEL`) |
| Voice output (British TTS) | Sum of `AudioUsageEvent.estimated_cost` where `operation = speech` (`OPENAI_TTS_MODEL`) |
| Audio cost | Voice input + voice output |
| AI cost · all sources | Text AI cost + audio cost |

Each source's percentage is its share of the all-sources total. Unknown costs are left out of the sums rather than counted as zero, and the pages show how many calls lack pricing or usage. Wherever a column says "Text AI cost" it means Correct/Translate only; "Total AI cost" always includes audio.

How requests are counted:

- One `UsageEvent` per Correct/Translate submission that passes form validation. Invalid forms (400) are not counted.
- `status` is `success`, `failed` (after acceptance, with a sanitised `error_code` such as `timeout`, `invalid_or_failed_response`, `refused`, `language`) or `rejected` (`rate_limit` or `duplicate`, before any provider call, so 0 tokens and $0). The success rate is successes ÷ (successes + failures).
- `request_type` is the button the visitor chose. Romanian sent to Correct stays `correction`, with `auto_translated` set and both provider calls summed.
- `model` is the configured `OPENAI_MODEL`; `response_model` is the name the provider returned.
- One `AudioUsageEvent` per transcription or speech request that reaches the quota check: `operation` (`transcription` or `speech`), `speech_target` (`correction`, `native` or `translation`), `model`, `voice`, `status` (`success`, `failed` after a provider call or empty transcript, `rejected` for `audio_rate_limit` at $0), `error_code`, input/output/total tokens, provider-reported `audio_seconds`, `estimated_cost`, audience, user or visitor, and the related `UsageEvent` when a speaker from a live correction was pressed. Malformed uploads and invalid tokens are refused without a ledger row. It has no fields for text, transcripts, audio or IP addresses.
- Both ledgers are append-only: the Django admin shows them read-only, they cannot be added or edited, and only superusers can delete them. Registered text events link to the saved `AssistantRequest` when there is one.
- Every figure is a database aggregate. Reports use conditional aggregation, correlated subqueries for per-row audio figures (so text and audio totals never multiply each other), indexes on both ledgers' time, user, visitor, audience, status, operation/type and model columns, and pagination.

The Django admin also lists text usage events (identity, type, status, error, model, tokens, text AI cost), audio usage events (id, time, operation, audience, user or visitor, speech target, model, voice, status, error, duration, tokens, audio AI cost; filters and search by username, email or visitor ID), anonymous visitors, and saved requests with their token and cost columns, all read-only.

## Anonymous visitors and privacy

- The first time a signed-out visitor presses Correct, Translate, the microphone or a speaker, the app creates an `AnonymousVisitor` with a random UUID4 and sets the first-party cookie `corect_visitor_id` (HttpOnly, SameSite=Lax, Secure when `DJANGO_DEBUG=false`, 12 months, refreshed on each anonymous request). Plain page views set no cookie.
- The UUID contains no personal data. An unknown or malformed cookie value is never adopted; a new random ID replaces it.
- Analytics never store IP addresses (raw or hashed) or any submitted, transcribed or generated text or audio. For anonymous visitors only the visitor ID, request type or audio operation, model, voice, prompt version, status/error code, token counts, audio duration, estimated cost and timestamps are kept. Registered history keeps working exactly as before.
- Rate limiting still uses the HMAC of `REMOTE_ADDR`; the analytics ID is not used for limits.
- **Conversion.** When a visitor with the cookie signs up, the visitor is linked to the new account (`converted_via=signup`); signing in to an existing account links it with `converted_via=login`. The first conversion wins; nothing is copied into the account's history and no events are duplicated. Events made after signing in carry the user and, while the cookie exists, the same visitor ID. The dashboard's signup conversion is visitors who signed up ÷ visitors first seen in the period.
- Deleting a user keeps their usage events for aggregate reporting but removes the link (`user` becomes empty); their saved history is deleted as before.
- The Settings page tells visitors about the cookie. Set `ANALYTICS_VISITOR_COOKIE=false` to stop visitor tracking entirely (requests are still counted, without a visitor). Depending on where the app is offered, analytics cookies may require consent; review this for your jurisdiction.

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

- **Speech to text:** the transcription response's `usage` of type `duration` gives the audio seconds. `cost = seconds ÷ 60 × per_minute`. If a model reports token usage instead, its `input_per_1m`/`output_per_1m` prices are used when configured.
- **Text to speech:** the speech request uses server-sent events; the final `speech.audio.done` event's `usage` gives input and audio output tokens. `cost = (input_tokens × input_per_1m + output_tokens × output_per_1m) ÷ 1,000,000`. If that event is missing, the audio is still returned and usage stays unknown.

Built-in audio prices (USD, <https://developers.openai.com/api/docs/pricing>, checked 13 September 2026): `gpt-transcribe` $0.0045 per audio minute; `gpt-4o-mini-tts` $0.60 per 1M text input tokens and $12.00 per 1M audio output tokens. To change them, set `OPENAI_AUDIO_PRICING` (it replaces the built-in table; `{}` leaves audio costs unknown while usage is still recorded; invalid values stop the app at startup):

```sh
OPENAI_AUDIO_PRICING={"gpt-transcribe": {"per_minute": "0.0045"}, "gpt-4o-mini-tts": {"input_per_1m": "0.60", "output_per_1m": "12.00"}}
```

### Historical data

Migration `analytics.0002_backfill_assistant_requests` creates one ledger row for each `AssistantRequest` saved before tracking existed, copying only exact facts: user, request type, status, error code, model, prompt version and the original timestamp (`is_backfilled=True`). Their tokens and cost stay unknown and are never reconstructed from text length. Anonymous usage before this feature was never stored, so it cannot be backfilled. There is no historical audio usage and none is backfilled. The migration skips requests that already have a ledger row, and reversing it removes only backfilled rows.

## Provider cost

Measured on 13 September 2026 at the prices above. The analytics dashboard reports the real totals by source.

| Request | Cost per request | Per 1,000 requests |
| --- | --- | --- |
| Short sentence, already correct | $0.00013 | $0.13 |
| Short sentence with errors | $0.00027 | $0.27 |
| Medium message (~160 characters) | $0.00088 | $0.88 |
| Long message (~620 characters) | $0.0028–0.0035 | $2.84–3.48 |
| Romanian sent to Correct (two calls) | $0.00030 | $0.30 |
| Translation | $0.00014 | $0.14 |
| Voice input, 4–6 second recording (`gpt-transcribe`) | $0.0003–0.00045 | $0.30–0.45 |
| British TTS of one 12-word sentence (`gpt-4o-mini-tts`, 84 input / 101 audio tokens) | $0.0013 | $1.26 |

Output tokens dominate text cost: the ~1,250-token instructions are almost entirely served from the provider's prompt cache, while a long message produces 2,300–2,900 output tokens (including reasoning). Without the cache, a short correction costs about $0.0005. At the default daily limit, one actor sending 100 long messages costs about $0.35 per day. Audio output tokens are the most expensive per token, so TTS is generated only on request and cached in the page.

## Tests

Backend tests (PostgreSQL must be running):

```sh
python manage.py test apps --settings=config.test_settings --noinput
```

Tests use dummy configuration and mocked OpenAI calls; they make no paid calls. `python manage.py test` also selects test settings by default, unless `DJANGO_SETTINGS_MODULE` is explicitly set. `--noinput` lets Django recreate a test database left behind by an interrupted run; test settings disable persistent database connections so teardown is not blocked. The tests cover supplied correction/translation fixtures, schema validation, unchanged correct text, silent capitalisation and punctuation, native versions, line-break and spacing normalisation, language routing and automatic translation, refusal/truncation/timeout handling, CSRF, escaping, signup, username or email sign-in, profile username/password changes, header sign-in state, ownership, persistence, mistake category pages, private statistics and concurrent database limits.

Analytics tests (`apps/analytics/tests/`) cover usage events for registered and anonymous corrections and translations, failures, rejections and automatic translation; real usage capture from provider responses (including rejected responses) with `store=False` intact; cost calculation, unknown pricing and cost recalculation; that anonymous text, response text and IP addresses are never persisted; visitor cookie creation, reuse and rotation; signup and sign-in conversion without duplicated events; staff-only access; period, audience, type and model filters; token and cost totals; constant query counts as report rows grow; the historical backfill; and the read-only admin. `test_audio_analytics.py` covers audio pricing and its validation, grand total = text + audio and audio = speech to text + text to speech, shares, unknown audio pricing, user and visitor reports with audio at constant query counts, detail pages and the read-only audio ledger admin.

Voice tests (`apps/assistant/tests/test_voice_transcription.py`, `test_voice_speech.py`) cover WebM/MP4/WAV uploads, English and Romanian transcripts, the configured transcription model, language hints and prompt, no uploaded filename sent to the provider, no persisted audio, transcript, filename or IP, no history entry, missing/invalid/mismatched/ogg/oversized uploads, timeouts, empty and too-long transcripts, voice rate limits separate from text limits, CSRF, visitor tracking on and off, and the audio ledger having no content fields. Speech tests cover signed-token playback, refusal of raw text, tampered, foreign, expired and wrong-target tokens, the configured TTS model and voice, MP3 over server-sent events, `BRITISH_TTS_INSTRUCTIONS` always being sent and ruling out an American accent, missing usage events, provider failures, rate limits, linking to the correction and visitor, speaker controls only on the correction and native sentences (never Romanian explanations or translations), and no provider call when results are rendered.

Optional browser tests (run them after, not in parallel with, the backend tests; both use the same test database):

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python manage.py test qa.browser_check --settings=config.test_settings --noinput
```

These run a temporary Django server against a PostgreSQL test database, mock only the AI and voice services (the voice test also mocks the microphone, `MediaRecorder` and audio playback in the browser), exercise real forms and HTMX, and write screenshots/layout measurements under ignored `artifacts/browser/`. They cover 375, 390, 430, 768, 1024 and 1440px, correction (including scrolling the result below the sticky header), translation, errors, the mobile menu, accounts, history, practice, JavaScript-disabled operation, and voice: the microphone inside the text box with an accessible name, recording, stopping, transcript insertion at the caret and counter update, speaker controls on the correction and the native version only when present, pause and replay from the page cache, and speakers working after an HTMX replacement. They require no API key and make no paid calls.

### Manual live smoke test

Run this with a real key before accepting a voice release. It makes a few paid calls and is not part of automated tests.

1. On <http://127.0.0.1:8000/> (or HTTPS), press the microphone, allow access, say a short English sentence and press the microphone again. Confirm the transcript appears in the text box.
2. Press Correct.
3. Press the speaker beside the correction. Listen carefully: the pronunciation must be clear, natural contemporary British English.
4. If a Native version appears, press its speaker and check the British pronunciation there too.
5. Speak a Romanian sentence and confirm the Romanian transcription, with diacritics, appears correctly.
6. Open `/admin/analytics/` and confirm the text AI cost, voice input (speech to text) cost, voice output (British TTS) cost, audio cost and all-sources total, and that the audio usage table shows the calls.

Listen explicitly for American pronunciation (for example rhotic "r" in "car" or "water", flapped "t" in "better", or American vowels in "can't" and "bath"). **If the speech sounds American, voice output is not complete, even though the endpoint returned audio.** British accent quality is a product acceptance criterion.

## Production operation

Deploy the application behind HTTPS with `DJANGO_DEBUG=false`, a strong unique secret, the actual allowed hosts, a private PostgreSQL connection and a production database password. The application enables secure cookies (including the visitor cookie), HTTPS redirects, HSTS, clickjacking protection and normal Django CSRF/escaping protections. The microphone requires HTTPS in production.

```sh
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
waitress-serve --listen=127.0.0.1:8000 config.wsgi:application
```

WhiteNoise serves versioned compressed assets. Configure TLS at a trusted reverse proxy and ensure the WSGI URL scheme is correct (for Waitress, use its trusted-proxy settings, scoped to your proxy). Do not indiscriminately trust `X-Forwarded-Proto`. Match proxy request timeouts to the AI timeout (at least 60 seconds by default; Romanian sent to Correct can make two provider calls) and allow request bodies of at least `VOICE_MAX_BYTES` for `/assistant/transcribe/`. Database backup/restore, HTTPS, secret rotation, scheduled cleanup and infrastructure monitoring are deployment responsibilities. Monitor 429/503 counts and sanitised `apps.assistant` and `apps.analytics` log codes; do not enable verbose OpenAI/HTTP logging in production. Keep `OPENAI_PRICING` and `OPENAI_AUDIO_PRICING` in step with the provider's published prices. Restrict staff status to people who may see usage and cost data.

The V1 settings are intentionally fixed. Voice input and British voice output are included; realtime voice conversation, social login, password-reset email delivery, public deployment and AI-generated practice are not. Saved activity counts are not an English proficiency score.

## Visual reference

The supplied mobile and desktop images guide the homepage hierarchy, blue/white palette, rounded editor/results, paired actions and navigation. The implementation uses actual responsive HTML, not a phone/browser frame. The editor begins empty and uses the specification's 2,000-character default. Long responses scroll naturally.

Front-end libraries are vendored in `static/vendor/`, so no runtime CDN or frontend build tool is required: HTMX 2.0.8 with its licence file, and Chart.js 4.5.0 (UMD build, MIT licence banner at the top of the file), loaded on the progress page and the staff analytics pages. Voice uses only native browser APIs (`static/js/voice.js`, `static/css/voice.css`).

## Implementation verification

Verified locally on 13 September 2026 with Python 3.11, Django 5.2.17 and PostgreSQL 16:

- 117 backend tests (49 application, 34 analytics, 34 voice and audio analytics) and 5 browser workflow tests passed.
- `makemigrations --check` found no missing migrations; `migrate` applied `analytics.0001`–`0003`, backfilling one ledger row per existing saved request (35 of 35, original timestamps, no tokens or cost).
- Django system checks and `check --deploy` with production settings passed.
- Live OpenAI verification is complete for text: the application has been tested successfully with `OPENAI_MODEL=gpt-5.6-luna`, including multi-line input, trailing whitespace, tense/time contradictions, silent capitalisation and punctuation, native versions (prepositions, American vocabulary, unnatural but correct text), Romanian sent to Correct, translation and a long message. These are spot checks, not a systematic linguistic evaluation.
- Live voice service check: `gpt-4o-mini-tts` with voice `cedar` returned MP3 over server-sent events (146 `speech.audio.delta` events and one `speech.audio.done` with usage: 84 input and 101 audio tokens, $0.0012624). `gpt-transcribe` transcribed that MP3 exactly and a Romanian sample with correct diacritics, reporting duration usage (4.00 s, $0.0003; 6.00 s, $0.00045). **The British accent has not yet been validated by a human listener**; complete the manual live smoke test above before accepting voice output.
- Live analytics smoke test on the development server: two anonymous corrections (one Romanian, auto-translated with both provider calls summed), signup from the same browser (visitor converted via signup), then a registered correction linked to its saved history. Events recorded real `gpt-5.6-luna` token usage and matching cost estimates; all five staff analytics pages returned 200 with no page errors; the test accounts and events were removed afterwards.
- Screenshots reviewed for the homepage (desktop and mobile), sticky header and editor, the microphone inside the text box at 1440, 390 and 375px (44×44px, clear of the counter), mobile drawer menu, history, mistake category pages, progress chart, profile and the analytics cost breakdown; no horizontal overflow. The mobile homepage fits without scrolling at 360×740, 375×667, 390×844 and 768×1024.
- Known issue: one long-message correction failed once with `invalid_or_failed_response` (the provider response did not pass validation); it did not recur in later runs.
- Axe-core 4.11 (no WCAG 2 A/AA or WCAG 2.1 AA violations on the homepage, signup, sign-in and settings at 390px and 1440px) predates the September interface changes and has not been re-run. Automated checks are not a complete accessibility certification.

Local screenshots and audit reports are under ignored `artifacts/`; rerun the browser command above to regenerate workflow screenshots.
