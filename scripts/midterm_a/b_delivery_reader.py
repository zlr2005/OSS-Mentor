"""Read normalized B tables into the record groups used by the 011 adapter."""
import hashlib
import json
from pathlib import Path

KINDS=('repositories','developers','files','code_documents','contributions','project_behaviors',
       'issues','issue_file_links','file_aliases')
IDENTITY_FIELDS={
    'repositories':('repository_key',),'developers':('developer_key',),'files':('file_key',),
    'code_documents':('file_version_key',),
    'contributions':('developer_key','repository_key','file_version_key','commit_sha','behavior_type'),
    'project_behaviors':('developer_key','repository_key','behavior_type','event_at','source'),
    'issues':('issue_key',),'issue_file_links':('issue_key','file_key','evidence_kind','available_at','source'),
    'file_aliases':('file_key','commit_sha','old_path','new_path'),
}


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def record_key(kind,row):
    values=[row[field] for field in IDENTITY_FIELDS[kind]]
    if kind=='project_behaviors' and row.get('commit_sha'):values.append(row['commit_sha'])
    if kind=='issue_file_links' and row.get('commit_sha'):values.extend([row.get('pr_number'),row['commit_sha']])
    return hashlib.sha256(canonical(values).encode()).hexdigest()


def load_records(root):
    """Return manifest and 011-compatible record groups; call verify.py first."""
    root=Path(root)
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema_version']!='b-profile-evidence-v1' or manifest['fixture_only'] is not False:
        raise ValueError('Unsupported normalized delivery')
    records={}
    for kind in KINDS:
        # This contribution-only delivery has no Issue/outcome data.
        if kind in {'issues','issue_file_links'}:
            records[kind]=[];continue
        records[kind]=[json.loads(line) for line in (root/'tables'/f'{kind}.jsonl').read_text(encoding='utf-8').splitlines()]
    for developer in records['developers']:
        locations=developer.get('evidence_locations',[])
        developer['identity_evidence_location']=locations[0] if locations else None
    return manifest,records


def read_code(root,code):
    if code['availability']!='available':return None
    root=Path(root).resolve();path=(root/code['content_location']).resolve()
    if not path.is_relative_to(root):raise ValueError('Code path escapes delivery')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=code['content_hash']:raise ValueError('Code content checksum mismatch')
    return data
