# Developer profile integration contract v0.5 (local candidate)

Status: local candidate, pending C and D review; not a team-approved freeze.
This branch now implements the service/storage boundary, authenticated HTTP
routes, public GitHub collection and static entry points. The copied profile
page is still a demo; enabling its API gateway is a separate next step.
D still owns `api.py`, OpenAPI, authentication and shared storage interfaces;
these local integration changes require D's approval before upstream merging.

## Identity, migration and ownership

- The internal owner is the integer `oss_user.user_id` from migration 007.
  It is not a new public user-ID contract. The plan calls for UUIDs across
  APIs; D must reconcile that with the current platform before exposing IDs.
- Original migration 009 and its opaque legacy `user_key` are preserved.
  Additive `009a_profile_user_identity.sql` adds nullable `user_id`, its
  unique index and foreign key. Legacy strings, including numeric-looking
  strings, are never interpreted as authenticated identities.
- The migration runner applies each filename once. 009a leaves 010 available
  for C. D must review this follow-up filename before upstream merge.
- A live account can own one profile. A bound profile cannot be reassigned by
  `save_profile`. Hard account deletion removes its owned profile and its
  evidence through a trigger plus existing cascading foreign keys.
- Soft-deleted accounts cannot be looked up or updated by the user-aware
  methods. Full soft-delete data purging remains part of D's account lifecycle.
- Legacy unbound profiles remain available to local/demo callers. Administrative
  ownership reconciliation, if needed, must explicitly verify the legacy owner.
- Service methods accepting `profile_key` are trusted internal operations,
  not authentication boundaries. Protected routes must derive the user from
  the current session, call `ProfileService.profile_for_user()`, and use that
  returned profile key. Never trust body/query `user_id`, `user_key` or
  `profile_key`. Generate the first profile key server-side.

## Storage and recommendation boundary

`ProfileService` depends on shared `ProfileStore`. The old
`ProfileStorage` import remains an alias. `SQLiteProfileStorage` implements
both the existing dataclass methods and the dictionary-based import workflow.
The service does not execute SQL or fetch GitHub data.

`profile_contract(profile_key)` converts saved data to `DeveloperProfileV2`.
The adapter maps the shared model's `profile_source="user"` to storage's
`"user_input"` and back. Persisted `field_metadata` is retained separately.
Existing matching code can continue using `profile_for_matching()`, which
normalizes skill keys; C still owns wiring this profile into recommendation
v0.5. Ranking weights are unchanged. Public recommendations and preset feedback
now reject user-bound profile keys instead of disclosing private profiles.

## Profile and suggestion payloads

`fixtures/contracts/v0.5/profiles.json` supplies the profile example, including
`field_metadata`; `github_user.json` supplies sanitized public evidence.
`profile_api.json` records the candidate routes, service response fields,
suggestion example and compatibility aliases. Service tests check their output
against required fields. `test_profile_api.py` additionally exercises real
localhost HTTP handlers with isolated SQLite and fixture-only GitHub responses.

Canonical persisted suggestion names are `field_name` and
`suggestion_source`. Additive aliases `field` and `source` remain for
existing consumers. Do not remove or rename existing response fields.
Nested `merge_preview.suggestions` are transient domain objects using
`field`/`source`; UI actions must use top-level persisted `suggestions`,
which include `profile_field_suggestion_id`.

Accept/reject returns `{profile, suggestion, suggestions}` reloaded from
storage. `suggestions` includes all statuses, and the resolved item includes
`resolved_at`. Import returns only pending items in its top-level list.
Re-resolving a completed item produces a state conflict. Acceptance rechecks
current locks and source priority, including manual edits since the preview.
Concurrent decisions use one SQLite BEGIN IMMEDIATE transaction spanning lookup,
validation, profile save and status change. Nested operations borrow that same
connection without committing individually. Tests cover one winner under
concurrent accept/reject and rollback if the final status update fails.

## Implemented protected routes (pending team review)

| Method | Path | Request | Service payload / proposed HTTP body |
|---|---|---|---|
| GET | `/api/v1/me/profile` | none | `{profile,suggestions,consent_version}` |
| PUT | `/api/v1/me/profile` | editable profile fields, optional locks, no owner identifiers | `{profile}` |
| POST | `/api/v1/me/profile/import-github` | `{"consent_version":"profile-import-consent-v0.1"}` | `{profile_key,github_import,merge_preview,persistence,suggestions,collection}` |
| POST | `/api/v1/me/profile/suggestions/{suggestion_id}/accept` | `{}` | `{profile,suggestion,suggestions}` |
| POST | `/api/v1/me/profile/suggestions/{suggestion_id}/reject` | `{}` | `{profile,suggestion,suggestions}` |

HTTP responses also contain api_version and request_id. The route strips
internal owner IDs, checks the session, rejects query parameters and client
identity/provenance fields, and validates the editable body. PUT assigns
user_input provenance to changed fields and preserves unchanged metadata.
Optional `locks` maps canonical field names (e.g. skills.Python) to booleans;
only existing editable fields may be locked. Create a profile with PUT first;
GET/import/decisions return 404 when the current user has no profile.

The import route obtains sanitized public GitHub data using the current user's
authorization. `import_github_profile(profile_key, github_payload)` remains
network-free. The HTTP route enforces consent before network access, obtains the
session credential, collects outside the write transaction, then rechecks the
session and current ownership before persisting. Tests do not use real credentials.

## OAuth credential and public collection lifecycle

The platform previously stored only a token HMAC, which cannot be used as an
access token. AuthService now keeps the actual token only in a thread-safe,
process-local cache keyed by its random session ID, expiring with the session.
SQLite still stores only the opaque digest. Logout removes the cached token.
After restart or on a different worker, manual authenticated profile access
remains possible but importing returns 503 and requires re-login. This is a
single-process local implementation, not a production distributed credential vault.

The collector verifies the token's /user ID against the OAuth identity and
then requests public repositories, public events, public repo metadata and
language byte counts. Repository requests carry no token, even if a caller
previously granted broader scopes. Redirects are refused; request and response
sizes/timeouts are bounded. Upstream bodies, emails, commit messages, issue
bodies and credentials are not retained. No private repository data is imported.

Limits: first 10 owned public repositories plus recent event repository names,
then at most 10 repositories total and one page of 100 public events. No
pagination or contributed file-path fetch is implemented. Missing commit counts
are not fabricated. Counts reflect observed events, not lifetime authored
contributions; language bytes do not measure proficiency. The collection
response records these limits explicitly.

References checked during implementation:
[GitHub event endpoints](https://docs.github.com/en/rest/activity/events) describe
the bounded, delayed public timeline; [repository endpoints](https://docs.github.com/en/rest/repos/repos)
define public repository and language-byte responses. Real GitHub OAuth/collection
still requires an authorized live smoke test; deterministic tests use HTTP doubles.

## Static entry points

`/profile`, `/profile.html`, profile.js and profile.css serve the original UI
draft. Its default gateway remains demo/localStorage until step 6. The homepage
labels that entry as a demo draft. Login assets are externalized to comply with
the existing self-only CSP. Successful OAuth callbacks requested by a browser
accepting text/html set the cookie and 303-redirect to the validated local
return_to; API callers still receive JSON. HTTPS configuration adds Secure to
the cookie, and access logs omit query strings. Profile writes reject foreign
Origin/Sec-Fetch-Site. This does not claim full browser profile acceptance.

## Errors and compatibility

Reuse platform codes rather than adding new variants: 401
`authentication_required`; 404 `not_found` for unknown profile or an item not
owned by that profile; 409 `state_conflict` for an already resolved item; 422
`profile_validation_failed` for validation, locked-field or priority rejection;
503 `service_not_ready` for unavailable storage/credentials. Upstream failures
map to 401/403/429/502 as appropriate without echoing upstream bodies.
Malformed JSON and content length use 400, oversized bodies 413 and wrong
media types 415; transport errors also carry matching request IDs.

Service exceptions are converted to the platform
`{error:{code,message,details?,request_id}}` envelope. API tests cover
unauthenticated requests, foreign-owned IDs, invalid input, missing profiles,
resolution replay and concurrency. `/api/v1/profiles` excludes every bound
profile (including legacy bindings) and retains anonymous local/demo profiles.

## PostgreSQL handoff and review record

D's PostgreSQL baseline is unchanged. It still needs B's 009 tables and an
equivalent nullable integer owner FK, unique owner index and profile cleanup
semantics. Translate the SQLite deletion trigger appropriately and run the same
contract/lifecycle tests against a real PostgreSQL adapter before claiming
backend parity.

- Change ID: CONTRACT-v0.5-PROFILE-INTEGRATION-001.
- Proposed by: B integration work; affected owners: B, C, D.
- Scope: ProfileStore extension, identity binding, additive suggestion fields,
  DeveloperProfileV2 adapter, authenticated profile routes, OAuth public collector,
  transaction scope and static entry points.
- Compatibility: keep 009 and legacy opaque keys, preserve old suggestion
  aliases, preserve local profiles and platform auth exports. OpenAPI, API,
  authentication and public-profile filtering are changed for D's review;
  ranking weights, CI and PostgreSQL baseline remain unchanged.
- Fixtures/tests: profile_api.json, platform integration tests, profile API and
  public GitHub collector tests.
- Approvals: pending C and D; no approval is implied by a local commit.
