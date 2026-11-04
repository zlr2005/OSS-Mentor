"""Scan tracked dataset files without logging matched credential values."""
from pathlib import Path
import json
import subprocess
from oss_mentor.datasets.publication_secrets import contains_secret


def main():
    result = subprocess.run(['git', 'ls-files', '-z', '--', 'datasets/'], check=True, capture_output=True)
    hits = []
    scanned = 0
    for raw in result.stdout.split(b'\0'):
        if not raw:
            continue
        path = Path(raw.decode('utf-8'))
        if not path.is_file():
            continue
        scanned += 1
        if contains_secret(path.read_bytes()):
            hits.append(path.as_posix())
    print(json.dumps({'status': 'failed' if hits else 'passed', 'files_scanned': scanned,
                      'affected_files': hits, 'scope': 'working files tracked under datasets; two alert pattern families only; Git history not scanned'}))
    return bool(hits)


if __name__ == '__main__':
    raise SystemExit(main())
