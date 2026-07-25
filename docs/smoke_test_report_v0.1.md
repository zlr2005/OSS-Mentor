# OSS-Mentor Collector Smoke Test Report v0.1

## 1. Test summary

| Item | Result |
|---|---|
| Test date | 2026-07-12 (Asia/Shanghai) |
| Target | `eslint/eslint` public repository |
| Collection run ID | `758d74fc-cf0c-447a-bbea-5c902adb87eb` |
| Authentication | Anonymous public API smoke test |
| GitHub API version | `2026-03-10` |
| Collector result | Success, 0 failed repositories |
| Raw files | 4 gzip JSON envelopes |
| Automated tests | 11 passed |
| PostgreSQL execution | Not run; local environment has no `psql` or Docker |

## 2. Endpoints verified

All four requests returned HTTP 200:

- `GET /repos/eslint/eslint`
- `GET /repos/eslint/eslint/community/profile`
- `GET /repos/eslint/eslint/languages`
- `GET /repos/eslint/eslint/labels?per_page=100`

Raw files were written under ignored local storage:

```text
data/raw/repository/eslint/eslint/2026-07-11/
data/raw/community_profile/eslint/eslint/2026-07-11/
data/raw/languages/eslint/eslint/2026-07-11/
data/raw/labels/eslint/eslint/2026-07-11/
```

## 3. Observed public metadata

| Field | Value |
|---|---|
| GitHub repository ID | `11061773` |
| Repository | `eslint/eslint` |
| Fork | `false` |
| Archived | `false` |
| License SPDX | `MIT` |
| Latest reported push | `2026-07-11T08:39:17Z` |
| Community health percentage | `87` |
| Contributing guide detected | `true` |
| Labels returned | `62` |
| Candidate labels observed | `contributor pool`, `good first issue`, `help wanted` |

Language bytes reported by the API:

| Language | Bytes |
|---|---:|
| JavaScript | 11,233,846 |
| TypeScript | 65,980 |
| EJS | 5,017 |
| HTML | 823 |
| Shell | 475 |

These values are raw API observations, not skill weights or task requirements.

## 4. Raw envelope checks

- 4 gzip files were readable and valid JSON;
- all envelopes recorded API version `2026-03-10`;
- all status codes were 200;
- response endpoint, URL, fetched time, rate-limit headers, request fingerprint and response SHA were present;
- no `Authorization`, `Bearer`, `access_token`, or token value was found in the envelopes;
- `data/` is ignored by Git and did not appear as a normal untracked path;
- rate-limit remaining values after requests were 39, 38, 37, and 36.

## 5. Automated test coverage

The standard-library test suite covers:

- Wave CSV selection and duplicate rejection;
- CLI dry-run without network access;
- refusal of real collection without `--allow-network`;
- Link header parsing and pagination;
- cross-origin pagination rejection;
- Raw gzip envelope and lineage metadata;
- refusal to persist secret query parameters;
- PostgreSQL migration table presence and foreign-key dependency order.

Command:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Result: 11 tests passed.

## 6. Remaining validation

The smoke test proves the repository-level Raw path only. It does not yet prove:

- PostgreSQL migration syntax on a real server;
- Raw-to-normalized database writes;
- Issue/PR filtering and history backfill;
- Timeline-based Issue–PR linking;
- Review, file, commit, and Check Run collection;
- ETag `304` behavior against a persisted cache;
- resume behavior after a partially failed Wave run.

The next implementation step is to normalize the four repository endpoints into PostgreSQL and then add Issue/PR collection for one Wave 1 repository.
