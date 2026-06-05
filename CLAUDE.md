# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A topic-subscription daily-digest system. It searches news per enabled topic, dedupes, scores, generates a Chinese HTML digest, and sends it via Gmail SMTP. "AI" and "Football" are only the default seeded topics, not hard-coded limits — any topic (Minecraft, Finance, etc.) can be added through the web admin. The README is in Chinese and is the authoritative operational reference; this file covers architecture and commands.

## Commands

All commands assume the venv at `.venv` (Python 3.10+):

```bash
source .venv/bin/activate            # or prefix commands with .venv/bin/python

.venv/bin/python main.py --dry-run   # build digest, print to stdout, do NOT send
.venv/bin/python main.py --send      # build digest and send via Gmail SMTP
.venv/bin/python main.py --init-db   # initialize/migrate SQLite (equiv: python -m src.database)

.venv/bin/uvicorn web:app --host 127.0.0.1 --port 8001   # web admin

.venv/bin/python -m pytest -q                            # run all tests
.venv/bin/python -m pytest tests/test_scorer.py -q       # single test file
.venv/bin/python -m pytest tests/test_scorer.py::test_freshness_score_uses_expected_buckets  # single test
```

There is no lint/format/build step configured; pytest is the only check.

## Architecture

Two entry points share the same `src/` core:
- `main.py` — CLI for the digest run (cron-driven in production).
- `web.py` — FastAPI app. It re-points its own `__path__` at `web/` so `from web.routes import router` resolves, then mounts `/static` and includes the router. The admin UI is FastAPI + Jinja2 templates (`templates/`), no React/Vue.

### Digest pipeline (`src/runner.py` `run_digest`)

This is the heart of the system. One run produces a single multi-topic digest:

1. Create a `Run` row immediately (so failures are recorded), wrapped in try/except that always finalizes `success`/`error_message`.
2. `apply_db_settings` overlays DB-stored `settings` (mail_to, summary_style) onto the frozen `.env` `Settings` dataclass via `dataclasses.replace`.
3. Load enabled topics (ordered by priority desc), plus all enabled keywords/query-templates and all sources, grouped by `topic_id`.
4. `fetch_news` (`src/searcher.py`) → `filter_new_items` (`src/dedupe.py`) → `score_and_store_items` (writes `NewsItem` rows, drops items below `topic.min_score`) → `select_items` (top `daily_limit` per topic).
5. `summarize_digest` (`src/summarizer.py`) calls the LLM; `render_email` (`src/renderer.py`) builds HTML; `make_subject` builds the title.
6. Only when `send=True`: resolve recipients, send, archive HTML, `mark_sent`, persist `final_summary` back onto `NewsItem` rows.

`auto_send_enabled` (DB setting) gates `--send`; a false value raises and skips sending.

### Topics are data, not code

The "generic topic" design means there are NO hard-coded AI/Football branches in the runtime path. AI and Football exist only as seeded rows (`DEFAULT_TOPICS` etc. in `src/database.py`). Each topic owns its `daily_limit`, `min_score`, `priority`, `summary_style`, and its own keywords / query templates / source scores. Adding a topic = inserting rows, done via the web admin or `src/topic_generator.py` (LLM proposes a topic spec, user previews, then it is written).

### Database & migrations (`src/database.py`)

SQLModel over SQLite at `data/app.sqlite3`. `init_db` is idempotent and is called from both entry points and again inside `run_digest`. It runs a multi-step migration on every start, so be careful editing it:
- It detects legacy tables that had a `category` column (the old AI/Football-only schema) and renames them to `*_legacy_category`, then copies rows forward, mapping `category` → the new `topic_id` of the seeded AI/Football topics. Legacy tables are preserved, never dropped.
- `runs` similarly migrates from fixed `ai_count`/`football_count` columns to a JSON `topic_counts` field; old table becomes `runs_legacy_fixed_counts`.
- A standalone legacy `data/sent_news.sqlite3` is migrated into the new `sent_news` table.
- `seed_defaults` only inserts defaults that don't already exist (idempotent), and only seeds default topics when `topics` is empty.

The raw `migrate_*` functions use `sqlite3` directly (not SQLModel) so they can introspect/rename arbitrary legacy tables.

### Search & Tavily failover (`src/searcher.py`, `src/tavily_key_manager.py`)

`fetch_news` iterates topics by priority; per topic it builds queries (enabled query templates first, else keyword-derived queries by language), fetches `daily_limit * 2` candidates, and dedupes by URL within the topic. A failing topic or query is logged and skipped, never fatal.

`TavilyKeyManager` rotates multiple keys with DB-backed status (`tavily_key_status`, storing only a 12-char SHA-256 fingerprint, never the key). On failure it classifies the error: `401/403` → `invalid` (skip permanently), `429` → `rate_limited` (disabled 1 hour), `5xx`/network → `transient_error` (try next key). A `TypeError` is re-raised (it signals an incompatible `tavily-python` API, not a key problem).

### Scoring (`src/scorer.py`)

`final = source*0.35 + freshness*0.25 + keyword*0.25 + duplicate*0.10 + preference*0.05`. Source score resolution: topic-specific source row → global source row (`topic_id is None`) → default 45. A matched-but-disabled source returns `None`, which drops the item entirely. Items scoring below `topic.min_score` are filtered out in `score_and_store_items`.

### Dedupe (`src/dedupe.py`)

URL-first, global. `normalize_url` strips tracking params (utm_*, fbclid, etc.), trailing slashes, sorts query params, and lowercases host. `sent_news.url` is globally unique, so a URL sent in any prior digest never reappears — even across topics. Within one digest, titles ≥0.9 similar (SequenceMatcher) are also deduped. To allow the same URL under multiple topics, you'd change the `sent_news` uniqueness and `filter_new_items` to key on `(topic_id, url)`.

### Summarization (`src/summarizer.py`)

Calls an OpenAI-compatible chat API (DeepSeek by default) requesting a strict JSON digest. If `LLM_API_KEY`/`OPENAI_API_KEY` is empty OR the call/parse fails, it degrades gracefully to `_fallback_digest` built from raw Tavily snippets — the run still succeeds. When the base URL contains "deepseek", it passes `extra_body.thinking` per the `DEEPSEEK_THINKING` flag.

### Web auth (`src/security.py`, `web/deps.py`)

Single admin user from `.env` (`ADMIN_USERNAME`/`ADMIN_PASSWORD`), constant-time compared. Session is a signed (not encrypted) `itsdangerous` token in the `afd_session` cookie; `require_admin` validates it and redirects to `/login` otherwise. No CSRF protection yet — the app expects to sit behind an SSH tunnel or authenticated reverse proxy. Mutating routes are POST-only.

## Conventions & constraints

- Config: secrets are read ONLY from `.env` into the frozen `Settings` dataclass (`src/config.py`). `LLM_*` is preferred with `OPENAI_*` as fallback; `openai_*` fields mirror the resolved `llm_*` values. The web UI never displays secret values. DB `settings` rows hold only non-secret operational config (mail_to, send_time, summary_style, auto_send_enabled) and are overlaid at runtime.
- Error redaction: `redact_error` (runner) and `sanitize_error` (key manager) scrub all known secrets from messages before they hit logs, the `runs` table, or `tavily_key_status.last_error`. Preserve this when adding error handling.
- Times are stored UTC (`utc_now`); display/scheduling uses `TIMEZONE`.
- UI text, prompts, and digest output are Chinese — match that when editing templates or LLM prompts.
- `tests/` use pytest with no config file or conftest; they call `init_db` against real temp SQLite paths and use `SimpleNamespace` to fake model objects.
