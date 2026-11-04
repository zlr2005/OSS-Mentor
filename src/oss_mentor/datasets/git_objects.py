"""Read Git objects without checkout, filters, mailmap, or executing repository code."""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def object_hash(kind: str, content: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(content)}\0".encode() + content).hexdigest()


def parse_commit(content: bytes) -> dict:
    headers = content.split(b"\n\n", 1)[0].splitlines()
    values = {}
    parents = []
    for line in headers:
        name, _, value = line.partition(b" ")
        if name == b"parent":
            parents.append(value.decode("ascii"))
        elif name in (b"tree", b"author", b"committer"):
            values[name.decode()] = value
    for role in ("author", "committer"):
        match = re.fullmatch(rb"(.*) (-?\d+) ([+-]\d{4})", values[role])
        if match is None:
            raise ValueError("Malformed Git identity/timestamp")
        values[role + "_identity"] = match[1]
        values[role + "_at"] = datetime.fromtimestamp(int(match[2]), timezone.utc).isoformat()
    return {"tree": values["tree"].decode("ascii"), "parents": parents,
            "author_identity": values["author_identity"], "committer_identity": values["committer_identity"],
            "author_at": values["author_at"], "event_at": values["committer_at"]}


def parse_tree(content: bytes) -> list[tuple[str, str, str]]:
    result, position = [], 0
    while position < len(content):
        space = content.index(b" ", position)
        nul = content.index(b"\0", space)
        mode = content[position:space].decode("ascii")
        name = content[space + 1:nul].decode("utf-8")
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise ValueError("Unsupported Git tree filename")
        raw_id = content[nul + 1:nul + 21]
        if len(raw_id) != 20:
            raise ValueError("Truncated Git tree object")
        result.append((mode, name, raw_id.hex()))
        position = nul + 21
    return result


def flatten_tree(root_sha: str, read_object) -> dict:
    result = {}

    def visit(sha: str, prefix: str):
        for mode, name, child in parse_tree(read_object("tree", sha)):
            path = prefix + name
            kind = "tree" if mode in ("40000", "040000") else "commit" if mode == "160000" else "blob"
            result[path] = {"mode": mode, "type": kind, "sha": child}
            if kind == "tree":
                visit(child, path + "/")
    visit(root_sha, "")
    return result


def changed_files(before: dict, after: dict) -> list[dict]:
    """File differences; exact unique blob moves have explicit tree evidence.

    Similarity-based renames are deliberately left as delete/add pairs. Git does
    not store rename events, so an exact move is labelled as inferred evidence.
    """
    old = {p: e for p, e in before.items() if e["type"] != "tree"}
    new = {p: e for p, e in after.items() if e["type"] != "tree"}
    removed, added = set(old) - set(new), set(new) - set(old)
    renames = {}
    for path in sorted(added):
        matches = [p for p in removed if old[p] == new[path]]
        destinations = [p for p in added if new[p] == new[path]]
        if len(matches) == len(destinations) == 1:
            renames[path] = matches[0]
    moved_old = set(renames.values())
    result = []
    for path in sorted(set(old) | set(new)):
        if path in moved_old:
            continue
        if path in renames:
            result.append({"path": path, "status": "renamed", "previous_path": renames[path]})
        elif path in removed:
            result.append({"path": path, "status": "removed"})
        elif path in added:
            result.append({"path": path, "status": "added"})
        elif old[path] != new[path]:
            result.append({"path": path, "status": "modified"})
    return result


class GitObjectReader:
    """Use one cat-file batch process and retain the exact objects used as evidence."""

    def __init__(self, git_directory: Path):
        self.directory = git_directory.resolve()
        self.command = ["git", "--no-replace-objects", "--git-dir", str(self.directory)]
        self.assets: dict[str, bytes] = {}
        self.process = subprocess.Popen(self.command + ["cat-file", "--batch"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, kind: str, sha: str) -> bytes:
        if kind not in ("commit", "tree", "blob") or not re.fullmatch("[a-f0-9]{40}", sha):
            raise ValueError("Invalid Git object type/SHA")
        path = f"git_objects/{kind}/{sha}.bin"
        if path in self.assets:
            return self.assets[path]
        self.process.stdin.write((sha + "\n").encode())
        self.process.stdin.flush()
        header = self.process.stdout.readline().split()
        if len(header) != 3 or header[0].decode() != sha or header[1].decode() != kind:
            raise ValueError(f"Required historical Git object unavailable: {kind}/{sha}")
        size = int(header[2])
        if size > 16 * 1024 * 1024:
            raise ValueError("Git object exceeds this working collector's 16 MiB limit")
        content = self.process.stdout.read(size)
        if self.process.stdout.read(1) != b"\n" or object_hash(kind, content) != sha:
            raise ValueError("Git object hash mismatch")
        self.assets[path] = content
        return content

    def select(self, anchor: str, limit: int) -> list[str]:
        if not re.fullmatch("[a-f0-9]{40}", anchor) or not 1 <= limit <= 1000:
            raise ValueError("Require a full anchor SHA and commit limit between 1 and 1000")
        result = subprocess.run(self.command + ["rev-list", "--topo-order", f"--max-count={limit}", anchor],
                                check=True, capture_output=True, timeout=30)
        return result.stdout.decode("ascii").splitlines()

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
