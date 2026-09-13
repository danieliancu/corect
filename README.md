# Corect.uk

A lightweight British English assistant for Romanian speakers. Django templates and locally served HTMX provide an accessible, responsive editor; OpenAI runs exclusively on the server.

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

Set `OPENAI_API_KEY` to your API key and `OPENAI_MODEL` to a Responses API model that supports Structured Outputs. Both are required for live correction/translation; the application still starts and displays a friendly unavailable message without them. There is no fake production AI fallback.

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

Visit <http://127.0.0.1:8000/>. Admin is at `/admin/`. Creating a superuser is optional for ordinary use; signup is available in the application. No preconfigured application accounts are shipped.

## Configuration

| Variable | Meaning / default |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required random secret; keep private |
| `DJANGO_DEBUG` | `false` by default; example enables local development |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts, default `localhost,127.0.0.1` |
| `DATABASE_NAME`, `DATABASE_USER` | Default `englishcoach` |
| `DATABASE_PASSWORD` | Required database password |
| `DATABASE_HOST`, `DATABASE_PORT` | Default `127.0.0.1`, `5434` |
| `OPENAI_API_KEY` | Server-only credential |
| `OPENAI_MODEL` | Explicit model selection, no hidden default |
| `OPENAI_TIMEOUT` | Provider timeout, default 30 seconds |
| `ASSISTANT_MAX_CHARACTERS` | Maximum input length, default 2000 |
| `RATE_LIMIT_MINUTE`, `RATE_LIMIT_DAY` | Default 10/minute and 100/day per actor |

An actor is a signed-in user or an HMAC of an anonymous visitor's IP address. Forwarded headers are not trusted. If deploying behind a reverse proxy, configure the application server to establish the real `REMOTE_ADDR` only from that trusted proxy; otherwise all anonymous visitors share the proxy's quota. Never accept arbitrary client forwarding headers.

## Architecture and behaviour

- `core`: responsive homepage, shared navigation, fixed settings and assets.
- `accounts`: Django signup/sign-in/sign-out and profile email editing.
- `assistant`: Pydantic schemas, versioned prompts, independent string-based correction/translation services, OpenAI adapter, persistence and database-backed limits.
- `learning`: private history, category counts, repeated patterns, 30-day activity and a curated practice bank. Practice answers are checked on the server; no AI calls are made for practice.

`POST /assistant/correct/` and `POST /assistant/translate/` accept `text`, `submission_token` (UUID), and Django's CSRF token. Normal submissions return the homepage; `HX-Request: true` returns result HTML plus a fresh submission token. Error codes are 400 (validation), 409 (duplicate), 422 (language), 429 (quota) and 503 (service failure). GET never calls AI.

Each accepted action makes one `responses.parse` call with a Pydantic schema. Automatic SDK retries are disabled; requests are stateless and `store=False` is sent to OpenAI. No history is sent as context. The provider's own data policies still apply; `store=False` is not a promise of zero provider retention.

Correct preserves good English. The service forcibly restores the original when the model reports no genuine errors. British spelling suggestions remain separate, do not alter otherwise correct American English, and never count as mistakes. For text with genuine errors, linguistic quality still depends on the configured model and prompt; schema validation cannot prove grammar quality. A verb tense that contradicts a time expression ("I was there tomorrow") is always corrected, keeping the time expression. Besides the minimal correction, Correct returns an optional native version: how a British English speaker would naturally phrase it, with a short Romanian note. It is shown only when it differs from the correction, including for text that is grammatically correct but unnatural. Romanian input submitted to Correct is translated automatically, as a second provider call within the same accepted submission. Capitalisation and punctuation are fixed silently: they are never listed or highlighted as mistakes. Translation chooses direction from the predominant language.

Only authenticated successful requests save text/results. Grammar corrections are saved atomically with their parent request. Anonymous text/results are not written to the database. Authenticated failures store a sanitised code and metadata without text. User deletion cascades to saved requests. History and statistics always filter on the current authenticated user.

Atomic PostgreSQL counters prevent parallel requests from exceeding a quota. A unique actor/submission token prevents duplicate provider calls across workers. Claims are retained for seven days; expired counters and older claims should be cleaned daily:

```sh
python manage.py cleanup_assistant
```

Prompts are versioned in `apps/assistant/services/prompts.py`. Changing them requires reviewing the language examples. Future speech-to-text can pass its string directly to `CorrectionService.correct(text)` or `TranslationService.translate(text)`.

## Tests

Backend tests (PostgreSQL must be running):

```sh
python manage.py test apps.assistant.tests --settings=config.test_settings
```

Tests use dummy configuration and mocked OpenAI calls. `python manage.py test` also selects test settings by default, unless `DJANGO_SETTINGS_MODULE` is explicitly set. They cover supplied correction/translation fixtures, schema validation, unchanged correct text, language routing, refusal/truncation/timeout handling, CSRF, escaping, account flows, ownership, persistence, private statistics and concurrent database limits. These are contract tests, not live linguistic evaluations.

Optional browser tests:

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python manage.py test qa.browser_check --settings=config.test_settings
```

These run a temporary Django server against a PostgreSQL test database, mock only the AI service, exercise real forms and HTMX, and write screenshots/layout measurements under ignored `artifacts/browser/`. They cover 375, 390, 430, 768, 1024 and 1440px, correction, translation, errors, navigation, accounts, history, practice and JavaScript-disabled operation. They require no API key and make no paid calls.

For a deliberate live smoke check, configure the key/model, run the app, and submit the examples in `apps/assistant/tests/examples.py`, especially both unchanged-English examples. This incurs normal API charges and is separate from automated tests. Live quality has not been verified without configured credentials.

## Production operation

Deploy the application behind HTTPS with `DJANGO_DEBUG=false`, a strong unique secret, the actual allowed hosts, a private PostgreSQL connection and a production database password. The application enables secure cookies, HTTPS redirects, HSTS, clickjacking protection and normal Django CSRF/escaping protections.

```sh
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
waitress-serve --listen=127.0.0.1:8000 config.wsgi:application
```

WhiteNoise serves versioned compressed assets. Configure TLS at a trusted reverse proxy and ensure the WSGI URL scheme is correct (for Waitress, use its trusted-proxy settings, scoped to your proxy). Do not indiscriminately trust `X-Forwarded-Proto`. Match proxy request timeouts to the AI timeout. Database backup/restore, HTTPS, secret rotation, scheduled cleanup and infrastructure monitoring are deployment responsibilities. Monitor 429/503 counts and sanitised `apps.assistant` log codes; do not enable verbose OpenAI/HTTP logging in production.

The V1 settings are intentionally fixed. No voice recognition, social login, password-reset email delivery, public deployment or AI-generated practice is included. Saved activity counts are not an English proficiency score.

## Visual reference

The supplied mobile and desktop images guide the homepage hierarchy, blue/white palette, rounded editor/results, paired actions and navigation. The implementation uses actual responsive HTML, not a phone/browser frame. The editor begins empty and uses the specification's 2,000-character default. Long responses scroll naturally.

HTMX 2.0.8 is vendored with its licence in `static/vendor/`; no runtime CDN or frontend build tool is required.

## Implementation verification

Verified locally on 13 September 2026 with Python 3.11, Django 5.2.17 and PostgreSQL 16:

- Migrations applied successfully; no missing model migrations.
- 32 backend tests and 3 browser workflow tests passed.
- Screenshots reviewed at all six specified viewport widths; no horizontal overflow.
- Browser workflows included correction, translation, error recovery, long Romanian explanations, signup, history, practice, navigation and JavaScript-disabled forms.
- Django deployment checks passed using production settings.
- Axe-core 4.11 reported no WCAG 2 A/AA or WCAG 2.1 AA violations on the homepage, signup, sign-in and settings at 390px and 1440px. Automated checks are not a complete accessibility certification.
- Live OpenAI verification remains pending: no API key or model was configured. Language fixtures and browser AI outputs were mocked, not paid model evaluations.

Local screenshots and audit reports are under ignored `artifacts/`; rerun the browser command above to regenerate workflow screenshots.
