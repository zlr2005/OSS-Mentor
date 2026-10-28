# Midterm S0 contract freeze

Status: frozen by member D for B's answer, preview, confirmation and profile
version persistence work.

Base: `origin/main` at
`b1e137428acd66d8e806ca1df680c2609edef499` (PRs #19, #20 and #21 merged).
Contract version: `contracts-midterm-v0.6.0`.

## 1. Public types

`src/oss_mentor/contracts_midterm.py` is the only public midterm contract
module. B may keep its private dictionaries, but the platform adapter must map
them to these types at the boundary.

| Type | Purpose |
|---|---|
| `ProfileEvidence` | One attributable feature evidence row |
| `AssessmentSubmission` | Browser answers only; contains no owner fields |
| `ProfileFeature` | One preview feature row |
| `ProfilePreview` | Persisted preview with server-owned IDs/version basis |
| `ProfileFeatureStateRow` | One current/confirmed feature row |
| `ProfileFeatureState` | Versioned complete feature state |
| `FeatureValueSnapshot` / `FeatureProposal` | Current and proposed values in a confirmation suggestion |
| `ConfirmationSuggestion` | One stable suggestion and its conflict/lock status |
| `ConfirmationPlan` | Persisted server-owned plan |
| `ConfirmationDecision` | One explicit `accept` or `reject` decision |
| `AssessmentConfirmation` | Immutable confirmation result and new profile snapshot |
| `EnhancedProfileFeatures` | Confirmed, ordered feature export consumed by C |

`ProfileEvidence.details` preserves source-specific audit fields such as
repository, file version, commit SHA, event time and evidence location without
expanding the public identity contract.

## 2. Identity mapping

- Session resolves `oss_user.user_id`; this integer is the only ownership
  boundary.
- `profile_user_binding.user_id` remains unique: one platform user owns one
  `developer_profile`.
- `profile_key` is the stable UUID/public key stored on
  `developer_profile`; the server creates or resolves it and the browser may
  only reference it, never choose another user's key.
- `developer_key` is a separate, optional external dataset/training identity.
  It is not an authentication field and must not be inferred from
  `profile_key`. It remains `null` until a verified mapping exists.
- Browser requests must not contain `user_id`, `developer_key`, `profile_key`
  or `base_profile_version`. Those values are injected from session and
  storage.

## 3. SQLite migration freeze

Reserved order:

| Number | Owner | File |
|---|---|---|
| `011` | A | `011_midterm_datasets.sql` |
| `012` | B | `012_midterm_profile_assessment.sql` |
| `013` | C | `013_midterm_model_runs.sql` |
| `014` | D | `014_midterm_runtime_state.sql`, only if needed |

`012` is frozen for B. It may be developed and reviewed now, but must not merge
to `main` before `011` is registered/merged and the ordered migration tests
pass.

Reuse these existing tables:

- `oss_user`
- `developer_profile`
- `profile_user_binding`
- `profile_field_state`

Create these tables in `012`:

### `profile_questionnaire`

- PK/unique: `(questionnaire_id, questionnaire_version)`
- `schema_version`, `feature_schema_version`
- `definition_json`, `definition_sha256`, `created_at`

### `profile_assessment_submission`

- PK: `submission_id` (opaque text)
- `user_id` FK `oss_user(user_id) ON DELETE CASCADE`
- `developer_profile_id` FK `developer_profile ON DELETE CASCADE`
- questionnaire identity, `submitted_at`, `answers_json`
- `idempotency_key`, `created_at`
- UNIQUE `(user_id, idempotency_key)`

### `profile_assessment_preview`

- PK: `preview_id`
- user/profile/submission FKs
- `base_profile_version`
- questionnaire/feature schema versions and `submitted_at`
- `preview_json`, `preview_sha256`, `created_at`

### `profile_assessment_preview_feature`

- PK `(preview_id, feature_name)`
- `value`, `confidence`, `observed`, `locked`, `conflict`
- `source_kind`, `blocked_reason`, `evidence_json`

### `profile_assessment_plan`

- PK: `plan_id`
- user/profile/preview FKs
- `profile_key`, `base_profile_version`
- questionnaire/feature schema versions
- `plan_json`, `plan_sha256`, `status`, `created_at`
- status is `open`, `completed` or `stale`

### `profile_assessment_suggestion`

- PK `(plan_id, suggestion_id)`
- `feature_name`, `current_json`, `proposed_json`
- `conflict`, `status`, `blocked_reason`, `created_at`

### `profile_assessment_confirmation`

- PK: `confirmation_id`
- plan/user/profile FKs
- `profile_key`, `base_profile_version`, `new_profile_version`
- `confirmed_at`, `result_json`, `result_sha256`
- `idempotency_key`, `created_at`
- UNIQUE `plan_id`
- UNIQUE `(user_id, idempotency_key)`

### `profile_assessment_decision`

- PK `(confirmation_id, suggestion_id)`
- suggestion foreign key to the plan suggestion
- `feature_name`, `decision`, `conflict_was_visible`, `decided_at`

### `developer_profile_feature_state`

- PK: `feature_state_id`
- `developer_profile_id`, `profile_key`, `profile_version`
- `feature_schema_version`, `confirmation_id`, `state_sha256`, `created_at`
- UNIQUE `(developer_profile_id, profile_version)`

### `developer_profile_feature`

- PK `(feature_state_id, feature_name)`
- `observed`, `value`, `locked`, `evidence_json`

### `developer_profile_current_feature`

- PK/FK: `developer_profile_id`
- `feature_state_id` FK and UNIQUE
- `updated_at`

History rows are immutable. Current state is changed only by updating this
pointer in the same transaction as confirmation.

All timestamps are UTC ISO 8601 text. All user-owned rows must have an explicit
delete path; hard user/profile deletion cascades through assessment, preview,
plan, confirmation and feature-state records.

## 4. Storage protocol

The frozen protocol is `MidtermProfileStore` in
`src/oss_mentor/storage/base.py`.

Key methods:

```python
transaction() -> ContextManager[MidtermProfileStore]

put_questionnaire(...)
get_questionnaire(...)

create_assessment_submission(...)
get_assessment_submission(...)

create_profile_preview(...)
get_profile_preview(...)

create_confirmation_plan(...)
get_confirmation_plan(...)

get_current_feature_state(...)
get_feature_state(...)

find_confirmation_by_idempotency_key(...)
commit_assessment_confirmation(...)

get_enhanced_profile_features(...)
```

All methods receive `user_id` from the platform service. The storage
implementation resolves the owned `developer_profile_id` and `profile_key`
internally. It does not accept arbitrary owner/profile identifiers from HTTP.

`create_assessment_submission()` is idempotent for the same
`(user_id, idempotency_key)` and payload. It returns the existing
`submission_id`; reusing the key for a different payload raises
`IdempotencyConflictError`.

`get_enhanced_profile_features()` accepts an optional verified
`developer_key`. Passing `None` is valid and expected for platform-only users
without an external dataset mapping. 012 does not persist an inferred
`developer_key = profile_key`; a later D-owned identity mapping can supply it.

## 5. Confirmation semantics

The HTTP/API boundary accepts only:

```text
plan_id + decisions + idempotency_key
```

The server loads the plan by `(user_id, plan_id)` and current feature state from
storage. It never accepts a client-supplied plan body, base version, proposed
value or owner field.

Required behavior:

1. Run confirmation inside `store.transaction()` using `BEGIN IMMEDIATE`.
2. Compare the plan's `base_profile_version` with the current profile version.
3. If stale, raise `StalePlanError`; the API maps it to HTTP 409
   `state_conflict`.
4. If `(user_id, idempotency_key)` already exists with the same payload,
   return the existing confirmation.
5. If the same key is reused with a different plan/decisions, raise
   `IdempotencyConflictError`; the API maps it to HTTP 409.
6. A plan can produce at most one confirmation.
7. Write the decision rows, immutable confirmed feature state and new current
   pointer in the same transaction.
8. Do not persist a partial state if any step fails.

## 6. Handoff to B

B may now implement:

- `db/sqlite/012_midterm_profile_assessment.sql`
- SQLite `MidtermProfileStore`
- mapping from the existing `oss_mentor.profiling` private dictionaries to
  `contracts_midterm.py`
- migration and business tests

B does not modify `api.py`, shared contracts, `storage/base.py`, OpenAPI or
web files. D will review the migration/storage PR and wire the HTTP routes after
the storage contract passes.

The shared migration registry assertion in
`tests/test_candidate_refresh_report.py` is owned by D for migration
coordination. It now derives the expected set from `db/sqlite/*.sql`; A and B
do not edit that whitelist when adding their reserved migrations.
