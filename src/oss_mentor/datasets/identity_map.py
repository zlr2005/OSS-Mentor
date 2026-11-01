"""Persistent source-local keys; no inferred cross-source developer identity merges."""

import json
from pathlib import Path

from oss_mentor.datasets.github_history import opaque_key


class IdentityMap:
    def __init__(self, path: Path, repository_id: int):
        self.path, self.repository_id = path, repository_id
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "schema_version": "midterm-a-identity-map-v1", "repository_id": repository_id,
            "developers": {}, "paths": {}, "rename_conflicts": [],
        }
        if self.data["repository_id"] != repository_id:
            raise ValueError("Identity map belongs to another repository")

    def developer(self, source_identity: str, *, github_id: int | None) -> str:
        scope = f"github:{github_id}" if github_id is not None else f"git-author:{source_identity}"
        mappings = self.data["developers"]
        if scope not in mappings:
            mappings[scope] = opaque_key("developer", "github", github_id) if github_id is not None else opaque_key(
                "developer", "git-author", self.repository_id, source_identity)
        return mappings[scope]

    def file(self, path: str, *, previous_path: str | None = None, commit_sha: str | None = None) -> str:
        mappings = self.data["paths"]
        if previous_path is not None:
            previous_key = self.file(previous_path)
            if path not in mappings:
                mappings[path] = previous_key
            elif mappings[path] != previous_key:
                conflict = {"previous_path": previous_path, "path": path, "commit_sha": commit_sha,
                            "previous_file_key": previous_key, "file_key": mappings[path]}
                if conflict not in self.data["rename_conflicts"]:
                    self.data["rename_conflicts"].append(conflict)
        if path not in mappings:
            mappings[path] = opaque_key("file", self.repository_id, path)
        return mappings[path]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
