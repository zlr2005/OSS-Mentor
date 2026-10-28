"""Standalone verifier for the normalized B delivery (standard library only)."""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys


def verify(root):
    root=Path(root).resolve()
    manifest_bytes=(root/'manifest.json').read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != (root/'manifest.sha256').read_text().split()[0]:
        raise ValueError('Manifest checksum mismatch')
    manifest=json.loads(manifest_bytes)
    if manifest['schema_version']!='b-profile-evidence-v1' or manifest['fixture_only'] is not False:
        raise ValueError('Wrong delivery schema/fixture flag')
    seen=set()
    for item in manifest['files']:
        path=(root/item['path']).resolve()
        if not path.is_relative_to(root) or item['path'] in seen:
            raise ValueError('Unsafe or duplicate asset path')
        seen.add(item['path']);data=path.read_bytes()
        if len(data)!=item['size_bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise ValueError('Asset checksum mismatch: '+item['path'])
    tables={}
    for kind,count in manifest['entity_counts'].items():
        if 'tables/'+kind+'.jsonl' not in seen:
            raise ValueError('Table omitted from checksum manifest: '+kind)
        rows=[json.loads(line) for line in (root/'tables'/f'{kind}.jsonl').read_text(encoding='utf-8').splitlines()]
        if len(rows)!=count:raise ValueError('Table count mismatch: '+kind)
        tables[kind]=rows
        for row in rows:
            for name,value in row.items():
                if name.endswith('_at') and value is not None:
                    if datetime.fromisoformat(value.replace('Z','+00:00')).utcoffset()!=timedelta(0):
                        raise ValueError('Non-UTC timestamp')
    indexes={}
    for kind,key in [('repositories','repository_key'),('developers','developer_key'),('files','file_key'),
                     ('code_documents','file_version_key'),('contributions','contribution_key'),
                     ('project_behaviors','behavior_key')]:
        indexes[kind]={r[key]:r for r in tables[kind]}
        if len(indexes[kind])!=len(tables[kind]):raise ValueError('Duplicate identity in '+kind)
    def ref(kind,key):
        if key not in indexes[kind]:raise ValueError('Broken '+kind+' reference')
        return indexes[kind][key]
    for kind,rows in tables.items():
        for row in rows:
            for field,target in [('repository_key','repositories'),('developer_key','developers'),('file_key','files')]:
                if field in row:ref(target,row[field])
            for field,value in row.items():
                locations=value if field=='evidence_locations' else [value] if field.endswith('_location') else []
                for location in locations:
                    if location and not str(location).startswith(('https://','http://')) and location not in seen:
                        raise ValueError('Unlisted local evidence/content: '+str(location))
    for code in tables['code_documents']:
        if ref('files',code['file_key'])['repository_key']!=code['repository_key']:
            raise ValueError('File/repository mismatch')
        if code['availability']=='available':
            if not code['content_location'] or hashlib.sha256((root/code['content_location']).read_bytes()).hexdigest()!=code['content_hash']:
                raise ValueError('Code content mismatch')
        elif code['availability']!='missing' or not code['exclusion_reason'] or code['content_location'] is not None:
            raise ValueError('Invalid missing code reason/content')
    for contribution in tables['contributions']:
        code=ref('code_documents',contribution['file_version_key'])
        if any(contribution[k]!=code[k] for k in ('file_key','repository_key','commit_sha')):
            raise ValueError('Contribution/version mismatch')
        if contribution['event_at']!=code['snapshot_at']:
            raise ValueError('Contribution/code time mismatch')
        if contribution['author_association_status']!=ref('developers',contribution['developer_key'])['identity_status']:
            raise ValueError('Author identity association mismatch')
    if tables['weak_interest_behaviors']:
        raise ValueError('This delivery does not include weak-interest acquisition')
    expected_quality={
        'missing_code_rate':sum(r['availability']=='missing' for r in tables['code_documents'])/len(tables['code_documents']),
        'confirmed_contribution_author_rate':sum(r['author_association_status']=='verified' for r in tables['contributions'])/len(tables['contributions']),
        'unique_developer_key_count':len(tables['developers']),
        'developers_with_file_contributions':len({r['developer_key'] for r in tables['contributions']}),
    }
    for name,value in expected_quality.items():
        if name in manifest['quality'] and manifest['quality'][name]!=value:
            raise ValueError('Quality statistic differs from records: '+name)
    return {'status':'delivery_verified','dataset_version':manifest['dataset_version'],
            'entity_counts':manifest['entity_counts'],'quality':manifest['quality'],
            'scope':'Checks delivered bytes, references and code versions; sanitized evidence projections are not raw API envelopes.'}


if __name__=='__main__':
    print(json.dumps(verify(Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parent),ensure_ascii=False,indent=2))
