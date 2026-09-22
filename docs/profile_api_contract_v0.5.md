# Developer profile integration contract v0.5 (local candidate)

Status: local candidate, pending C and D review; not a team-approved freeze.
This branch implements the service/storage boundary and its fixtures, not the
HTTP routes or a working profile page. D still owns `api.py`, OpenAPI and
shared storage interfaces. The proposed `ProfileStore` extension in this
integration branch requires D's approval before upstream merging.

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
v0.5. No ranking weights or recommendation routes change here.

## Profile and suggestion payloads

`fixtures/contracts/v0.5/profiles.json` supplies the profile example, including
`field_metadata`; `github_user.json` supplies sanitized public evidence.
`profile_api.json` records the candidate routes, service response fields,
suggestion example and compatibility aliases. Integration tests check actual
service output against its required fields; they are not HTTP endpoint tests.

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
Concurrent accept/reject atomicity is not established by this implementation:
profile save and status transition remain separate transactions. D/B must
address that before a multi-user HTTP deployment.

## Proposed protected routes (not implemented here)

| Method | Path | Request | Service payload / proposed HTTP body |
|---|---|---|---|
| GET | `/api/v1/me/profile` | none | `{profile}` |
| PUT | `/api/v1/me/profile` | editable profile fields, no owner identifiers | `{profile}` |
| POST | `/api/v1/me/profile/import-github` | `{"consent_version":"2026-07-29"}` | `{profile_key,github_import,merge_preview,persistence,suggestions}` |
| POST | `/api/v1/me/profile/suggestions/{suggestion_id}/accept` | `{}` | `{profile,suggestion,suggestions}` |
| POST | `/api/v1/me/profile/suggestions/{suggestion_id}/reject` | `{}` | `{profile,suggestion,suggestions}` |

D must apply the platform response/request-ID envelope, strip internal owner
identifiers, validate JSON and consent, and authorize each operation. Manual
edits must receive server-assigned provenance; clients cannot claim imported
evidence or confirmed provenance arbitrarily. GET/PUT service bodies above are
route wrappers, not existing service return envelopes.

The import route obtains sanitized public GitHub data using the current user's
authorization. `import_github_profile(profile_key, github_payload)` remains
network-free; consent enforcement and real collection are still integration
work. Private repositories and secrets must never enter the fixture or report.

## Errors and compatibility

Reuse platform codes rather than adding new variants: 401
`authentication_required`; 404 `not_found` for unknown profile or an item not
owned by that profile; 409 `state_conflict` for an already resolved item; 422
`profile_validation_failed` for validation, locked-field or priority rejection;
503 `service_not_ready` for unavailable storage/configuration. D handles
malformed JSON, media type, upstream permission/rate-limit/failure codes using
the existing platform conventions.

Service exceptions are not HTTP responses. D owns status conversion and the
`{error:{code,message,details?,request_id}}` envelope. API tests must cover
unauthenticated requests, foreign-owned IDs, invalid input, missing profiles,
resolution replay and concurrency. Existing `/api/v1/profiles` must keep
returning only demo/public data; do not expose authenticated private profiles
through the inherited unfiltered listing helper.

## PostgreSQL handoff and review record

D's PostgreSQL baseline is unchanged. It still needs B's 009 tables and an
equivalent nullable integer owner FK, unique owner index and profile cleanup
semantics. Translate the SQLite deletion trigger appropriately and run the same
contract/lifecycle tests against a real PostgreSQL adapter before claiming
backend parity.

- Change ID: CONTRACT-v0.5-PROFILE-INTEGRATION-001.
- Proposed by: B integration work; affected owners: B, C, D.
- Scope: ProfileStore extension, identity binding, additive suggestion fields,
  DeveloperProfileV2 adapter, proposed accept/reject routes.
- Compatibility: keep 009 and legacy opaque keys, preserve old suggestion
  aliases, preserve local profiles and platform auth exports; no HTTP routes,
  OpenAPI, ranking weights, CI or PostgreSQL baseline edited.
- Fixtures/tests: profile_api.json and profile platform integration tests.
- Approvals: pending C and D; no approval is implied by a local commit.
