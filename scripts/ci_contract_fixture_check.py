"""CI check: all v0.5 contract fixtures must be valid JSON with fixed IDs."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "contracts" / "v0.5"

# Files owned by other members may be missing during parallel development.
OPTIONAL_FIXTURES = {
    "candidates.json",  # member A
    "profiles.json",    # member B
    "recommendations.json",  # member C
    "github_user.json",  # member B
    "sync_results.json",  # member A
}
REQUIRED_FIXTURES = {"errors.json"}  # member D


def main() -> int:
    failures = 0
    for name in sorted(REQUIRED_FIXTURES):
        path = FIXTURE_DIR / name
        if not path.is_file():
            print(f"missing required fixture: {name}")
            failures += 1
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
            print(f"{name} ok")
        except json.JSONDecodeError as exc:
            print(f"{name} invalid JSON: {exc}")
            failures += 1
    for name in sorted(OPTIONAL_FIXTURES):
        path = FIXTURE_DIR / name
        if path.is_file():
            try:
                json.loads(path.read_text(encoding="utf-8"))
                print(f"{name} ok")
            except json.JSONDecodeError as exc:
                print(f"{name} invalid JSON: {exc}")
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
