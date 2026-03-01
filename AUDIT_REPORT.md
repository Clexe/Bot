# Comprehensive Bot Audit

## Scope and method
This audit covered architecture, correctness, reliability, security posture, operational readiness, and test quality by:

- Reading core runtime modules (`main.py`, `scanner.py`, `handlers.py`, `fetchers.py`, `database.py`, `filters.py`, `drawdown.py`).
- Running automated checks (`pytest -q`, `ruff check .`).
- Reviewing test coverage patterns and configuration surfaces.

## What is working well
- Core strategy/regime/scanner logic has broad unit-test coverage and currently passes test execution.
- The scanner has defensive logging and multiple safety filters (session filter, market-hours filter, correlation filter, news filter, cooldown dedupe).
- Database access uses a pooled connection model and helper wrappers rather than ad-hoc global connections.

## Findings (prioritized)

### 1) **High — SQL interval parameterization is incorrect in multiple queries**
Several SQL statements embed placeholders inside quoted interval literals like `INTERVAL '%s days'` and `INTERVAL '%s hours'`.

Why this is a problem:
- In psycopg2, `%s` placeholders should not be embedded inside SQL string literals.
- This can lead to SQL syntax/runtime errors or unexpected behavior depending on adaptation, especially under production data access patterns.

Where:
- `database.py` in `expire_stale_signals`, `get_signal_stats`, `get_pair_breakdown`, `get_session_breakdown`, `get_zone_type_stats`, `get_regime_stats`.

Recommended fix:
- Use arithmetic with typed intervals, e.g. `CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')` and `CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')`.

---

### 2) **High — Environment validation is incomplete for required market data dependencies**
Startup validation only checks `TELEGRAM_TOKEN` and `DATABASE_URL`.

Why this is a problem:
- Deriv connectivity depends on both `DERIV_APP_ID` and `DERIV_TOKEN`, but these are not validated at startup.
- If absent/misconfigured, Deriv fetch paths repeatedly fail at runtime, potentially causing silent degradation to empty DataFrames and no signals for affected symbols.

Where:
- `main.py` (`_validate_env`).
- `fetchers.py` (`DerivSession._connect` uses `DERIV_APP_ID`/`DERIV_TOKEN`).

Recommended fix:
- Validate required exchange credentials based on enabled/supported symbol classes before bot start.
- Fail fast with explicit diagnostics.

---

### 3) **High — Drawdown/risk shield state is global, not user-scoped**
The drawdown circuit breaker tracks daily/weekly P&L and streaks in module globals for the entire process.

Why this is a problem:
- One user's losses can throttle/pause signal delivery for everyone.
- Multi-tenant behavior is incorrect for a Telegram bot serving many chats.

Where:
- `drawdown.py` (`_daily_pnl`, `_weekly_pnl`, `_consecutive_losses`, `_pause_until` are global).

Recommended fix:
- Key drawdown state by user ID (or account ID) and persist to DB for restart-safe behavior.

---

### 4) **Medium — Admin controls fail silently for non-admin users / misconfigured admin**
Admin handlers return immediately for non-admins without feedback.

Why this is a problem:
- Operational confusion: commands appear broken rather than unauthorized.
- If `ADMIN_ID` is unset (default empty string), no one can use admin commands and there is no user-visible explanation.

Where:
- `handlers.py` in `broadcast_command` and `users_command`.
- `config.py` sets `ADMIN_ID` default to `""`.

Recommended fix:
- Return a clear `Unauthorized` message for non-admin callers.
- Validate `ADMIN_ID` format at startup and log a warning/error when missing.

---

### 5) **Medium — News parsing uses ambiguous field and timezone assumptions**
The news filter maps `event.find('country').text` to `currency` and then compares it to FX currency codes.

Why this is a problem:
- XML field naming may not always map to ISO currency codes (country != currency).
- Event times are parsed as naive datetimes and force-assumed UTC, which can produce blackout windows at the wrong times.

Where:
- `filters.py` in `fetch_forex_news` and `is_news_blackout`.

Recommended fix:
- Parse/normalize the true currency field from feed schema.
- Parse event timezone explicitly (or convert via known source timezone) before comparison.

---

### 6) **Medium — Resource lifecycle gap: Deriv WebSocket session is never explicitly closed on app shutdown**
A close method exists (`DerivSession.close`) but shutdown path does not call it.

Why this is a problem:
- Potential unclean shutdowns and dangling connections/tasks.
- Harder incident debugging around reconnect loops and task cancellation.

Where:
- `fetchers.py` (`DerivSession.close` exists).
- `main.py` shutdown path only closes DB pool.

Recommended fix:
- Add a fetcher shutdown hook and call it in `post_shutdown`.

---

### 7) **Medium — Lint quality baseline currently failing**
Static lint check reports multiple issues including unused imports/variables and ambiguous variable naming.

Why this matters:
- Reduces maintainability and can hide real defects.
- Signals drifting code hygiene and review discipline.

Where:
- `ruff check .` reports 30 issues across runtime and test modules.

Recommended fix:
- Add CI lint gate; clean existing issues and enforce on PRs.

---

### 8) **Medium — Test coverage misses operationally critical modules**
While strategy-like modules are well covered, there are no direct tests for command handlers, DB query layer behaviors, or startup/shutdown lifecycle.

Why this is a problem:
- Regression risk remains high in user interaction paths and persistence/reporting paths.
- SQL/runtime integration defects can pass unnoticed despite green unit tests.

Where:
- No direct tests in `tests/` for `database.py`, `handlers.py`, `main.py`, `fetchers.py` lifecycle/error paths.

Recommended fix:
- Add focused tests for:
  - DB query correctness and migration safety.
  - Handler auth and input validation.
  - Startup env validation and shutdown resource cleanup.

---

### 9) **Low — Documentation gap**
Project README is effectively empty.

Why this is a problem:
- Onboarding, operations, and incident response are slowed.
- Configuration expectations and deployment assumptions are undocumented.

Where:
- `README.md`.

Recommended fix:
- Add setup, env vars, architecture summary, runbook, and troubleshooting sections.

## Risk summary
- **Immediate reliability risks:** SQL interval query construction, missing env validation for Deriv.
- **Immediate product risk:** global drawdown state affecting all users.
- **Medium-term maintainability risk:** failing lint baseline + missing tests for operational modules + sparse docs.

## Suggested remediation order
1. Fix SQL interval queries and add regression tests for date-windowed stats queries.
2. Expand startup env validation and fail-fast diagnostics.
3. Refactor drawdown state to be user-scoped and persisted.
4. Add explicit shutdown for fetcher sessions.
5. Harden admin UX and auth feedback.
6. Restore lint cleanliness and enforce in CI.
7. Add missing tests for handlers/db/lifecycle.
8. Improve README and operational docs.
