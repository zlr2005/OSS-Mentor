"""Validate and unpack B/C evidence without overwriting different local files."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[2]


def main():
    package=ROOT/'handoff/bc_supplement_20261105'
    expected=json.loads((package/'summary.json').read_text())
    archive=package/'evidence.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected['archive_sha256']:
        raise ValueError('Archive hash mismatch')
    manifest=json.loads((package/'file_manifest.json').read_text())['files_sha256']
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist())!=set(manifest):raise ValueError('Archive inventory mismatch')
        for name,digest in manifest.items():
            target=(ROOT/name).resolve()
            if not target.is_relative_to(ROOT) or '.git' in target.relative_to(ROOT).parts:
                raise ValueError('Invalid destination')
            if hashlib.sha256(z.read(name)).hexdigest()!=digest:raise ValueError('Evidence hash mismatch')
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                raise ValueError('Different existing file: '+name+'; use a clean checkout')
        for name in manifest:
            target=ROOT/name
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
    print('Verified and unpacked '+str(len(manifest))+' evidence files')


if __name__=='__main__':main()
