# Step 6: authenticated profile page integration

Verified locally on 2026-09-22, integration branch `feat/v05-profile-integration`.
Base: `46d4ed56877bbc6d81d014093899a1b14c1ba08c`.

## Implementation

- Default GET/PUT/import/accept/reject use the authenticated API. `?mode=demo`
  explicitly opts into fixtures/localStorage; failures never select it automatically.
- Editable PUT allowlist excludes identity and provenance; includes existing-field locks.
- Read restores persisted suggestions. Decisions consume server-returned profiles and statuses.
- New users start with an unsaved form on GET 404. Login failures disable editing and clear
  account state. Errors retain HTTP status/request ID and offer login/reload navigation.
- Saving, importing and deciding are serialized within a page. Import/decisions require a
  saved form without unsaved edits. Zero skill levels, locks, non-rendered skills and unchanged
  metadata survive form collection. Empty preference sets fail validation; names max 60.
- Evidence preserves all returned fields. Priority conflicts are not mislabeled as locks.
- Public sampling limitations are visible. Historical import summaries are not restored by
  the current GET contract; persisted suggestions are restored. No browser cache of account data.

## Reproducible checks

From the integration checkout, Python 3.11.9 in its `.venv`:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -q
node --test tests/profile_gateway.test.cjs
node --check web/assets/profile.js
.venv/Scripts/python.exe -m compileall -q src scripts tests
git diff --check
```

This run: **512 Python tests OK (52.930 s); 12 Node tests passed**. Compilation,
JavaScript syntax and whitespace checks passed. Node tests are currently a separate
command, not added to D's shared CI workflow.

Browser fixture command:

```powershell
.venv/Scripts/python.exe tests/profile_browser_fixture.py
```

Only binds `127.0.0.1:8876`, uses a disposable SQLite database, test account and
deterministic GitHub fixture source. `/test/session` exists only in this standalone
test harness, never in the production handler. Stop with Ctrl+C after testing.

Actual in-app browser checks (real page, HTTP routes and SQLite, mocked GitHub only):

1. Anonymous GET: login error/request ID, disabled writes and visible login link.
2. Test account GET 404: create `Step6 Fixture`, save and reload; name retained.
3. Explicit consent/import: six fixture suggestions; counts, language percentages,
   confidence, timestamps, source, repository/path evidence visible.
4. Accept build_tooling suggestion: level becomes 1; reject documentation suggestion.
   Reload/all filter preserves accepted/rejected states and build_tooling level 1.
5. Save Python level 0 with lock; reload retains 0 and checked lock.
6. Uncheck all preferred languages: save blocked by validation.
7. Unsaved edits block import; unrelated edits preserve accepted-field provenance display.
8. Explicit demo save `Demo Only Fixture`; return to API still shows `Step6 Fixture`.
9. Browser error logs empty during the verified account flow. Screenshot inspected at
   form validation area; this is not comprehensive responsive/accessibility acceptance.

Temporary test pages/server were closed after checks. No real GitHub account was used.
HTTP 503/409/422/429, malformed responses and network failures were adapter-tested;
they are not all claimed as manually exercised browser scenarios.

## Outstanding

Step 6 is complete for local API wiring. Step 7 still needs authorized real OAuth
and public-data collection acceptance, broader session/error/responsive coverage and
recommendation-side consumption with C/D. Shared contract approval, Node CI integration,
production token storage, PostgreSQL parity and release remain outstanding. Multiple-tab
optimistic concurrency/versioning is not implemented; reload after stale-conflict errors.
No changes to ranking weights, PostgreSQL, CI or deployment were made here.

Original `C:\oss\OSS-Mentor` remains clean on `feat/v05-profile-ui` at
`41f9510fdd17879771b873b50fc0f2715ffd71f1`; new functionality exists only in the
integration copy. No push or remote PR changes.
