"""Package used evidence at original paths; original source trees remain partial."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'data/midterm/working'
DEST=ROOT/'handoff/bc_supplement_20261105'


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def rows(path):return [json.loads(l) for l in path.read_text(encoding='utf-8-sig').splitlines() if l.strip()]


def main():
    files=set();roots=set()
    def add(path,required=True):
        path=path.resolve()
        if not path.is_relative_to(ROOT):raise ValueError('Dependency outside repository')
        if not path.is_file():
            if required:raise FileNotFoundError(path)
            return
        if path.suffix.lower() in ('.sqlite','.sqlite3','.env'):raise ValueError('Database or credentials file excluded')
        files.add(path)
    def root(path):
        path=path.resolve()
        if path in roots:return
        roots.add(path);add(path/'manifest.json')
        for name in ('source_manifest.json','collection_plan.json','summary.json','verified_outcomes.jsonl',
                     'historical_contributions.jsonl','historical_controls.jsonl','file_contributions.jsonl',
                     'comparison_groups.jsonl','reused_contribution_index.jsonl'):
            add(path/name,False)
        for name in ('manifest.json','source_manifest.json','collection_plan.json'):
            p=path/name
            if not p.exists():continue
            data=read(p)
            for rel in data.get('source_roots',{}).values():
                candidate=ROOT/rel
                if (candidate/'manifest.json').exists():root(candidate)
            for rel in data.get('input_manifest_sha256',{}):
                candidate=ROOT/rel
                if (candidate/'manifest.json').exists():root(candidate)
    delivery=BASE/'sparse_expansion_20261104_handoff_v6'
    profiles=BASE/'sparse_review_profiles_20261105_v1'
    for directory in (delivery,profiles):
        root(directory)
        for p in directory.iterdir():
            if p.is_file():add(p)
    root(BASE/'merged_ab_history_2024_2026_v6')
    # Outcome bodies and actual-closing-event responses.
    for row in rows(delivery/'outcome_index.jsonl'):
        origin=ROOT/row.get('evidence_source_root',row['source_root']);root(origin);add(origin/row['evidence_ref'])
    # Code selected for profiles, retaining original evidence locations.
    pool={}
    for rel in read(profiles/'manifest.json')['input_manifest_sha256']:
        origin=ROOT/rel;root(origin)
        merged=(origin/'file_contributions.jsonl').exists()
        mapping=read(origin/'manifest.json').get('source_roots',{})
        for c in rows(origin/('file_contributions.jsonl' if merged else 'historical_contributions.jsonl')):
            sources=c.get('sources',[]) if merged else [c]
            for s in sources:
                if not s.get('evidence_ref') or not s.get('content_location'):continue
                actual=ROOT/mapping[s['source']] if merged else origin
                key=(c['repository'].casefold(),c['commit_sha'],c['path'],(actual/s['evidence_ref']).resolve())
                pool[key]=(actual,s)
    def code(origin,c):
        root(origin)
        for name in ('content_location','evidence_ref','blob_evidence_ref','listing_evidence_ref'):
            if c.get(name):add(origin/c[name])
    for group in rows(profiles/'profile_input_index.jsonl'):
        for c in group['records']:
            location=(ROOT/c['evidence_location']).resolve()
            key=(c['repository_key'].casefold(),c['commit_sha'],c['file_key'],location)
            if key not in pool:raise ValueError('Unresolved profile code dependency')
            origin,original=pool[key];code(origin,original)
    # Control evidence and all code rows that the join verifier may use.
    for pair in rows(delivery/'comparison_index.jsonl'):
        origin=ROOT/pair['history_source_root'];root(origin)
        controls={(r['issue_key'],r['as_of']):r for r in rows(origin/'historical_controls.jsonl')}
        for key in pair['comparison_issue_keys']:add(origin/controls[key,pair['as_of']]['evidence_ref'])
        for c in rows(origin/'historical_contributions.jsonl'):
            if c['github_numeric_id']==pair['github_numeric_id'] and c['as_of']==pair['as_of']:code(origin,c)
    # Metadata enrichment can be read without publishing teacher raw responses.
    for name in ('candidate_context_enrichment_20261104_v1','teacher_language_context_20261104_v1'):
        for p in (BASE/name).iterdir():
            if p.is_file():add(p)
    # References in all manifest files are retained; unused raw files are omitted.
    DEST.mkdir(parents=True,exist_ok=True)
    manifest={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    (DEST/'file_manifest.json').write_text(json.dumps(dict(evidence_subset=True,files_sha256=manifest),indent=2),encoding='utf-8')
    target=DEST/'evidence.zip'
    secret=re.compile(rb'(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,}|sk-proj-[A-Za-z0-9_-]{40,})')
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
        for p in sorted(files):
            data=p.read_bytes()
            if secret.search(data):raise ValueError('Possible credential detected; do not upload archive')
            archive.writestr(p.relative_to(ROOT).as_posix(),data)
    summary=dict(files=len(files),uncompressed_bytes=sum(p.stat().st_size for p in files),archive_bytes=target.stat().st_size,
        archive_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),evidence_subset=True)
    (DEST/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary))


if __name__=='__main__':main()
