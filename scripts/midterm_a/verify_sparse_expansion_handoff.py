"""Replay outcome evidence and verify supplementary history/control joins."""
import argparse
from collections import defaultdict
from pathlib import Path
import json

import collect_targeted_adapter as t


def main(version):
    b=t.b
    base=b.ROOT/'data/midterm/working'
    out=base/f'sparse_expansion_20261104_handoff_v{version}'
    manifests={}
    def manifest(root):
        root=root.resolve()
        if not root.is_relative_to(b.ROOT):raise ValueError('Source outside project')
        if root not in manifests:manifests[root]=b.read(root/'manifest.json')
        return manifests[root]
    def checked(root,relative):
        root=root.resolve();path=(root/relative).resolve()
        if not path.is_relative_to(root) or b.digest(path.read_bytes())!=manifest(root)['files_sha256'][relative]:
            raise ValueError('Evidence file hash mismatch')
        return b.read(path)
    for name in manifest(out)['files_sha256']:checked_bytes=out/name;assert b.digest(checked_bytes.read_bytes())==manifest(out)['files_sha256'][name]
    sources=b.read(out/'source_manifest.json')
    for name,rel in sources['source_roots'].items():
        assert b.digest((b.ROOT/rel/'manifest.json').read_bytes())==sources['source_manifest_sha256'][name]
    def find_pr(value,url):
        if isinstance(value,dict):
            if value.get('url')==url and 'closingIssuesReferences' in value:return value
            for child in value.values():
                result=find_pr(child,url)
                if result:return result
        elif isinstance(value,list):
            for child in value:
                result=find_pr(child,url)
                if result:return result
    rows=b.rows(out/'outcome_index.jsonl');index={r['outcome_id']:r for r in rows}
    assert len(index)==len(rows)
    targets=dict(sparse_ids={r['github_numeric_id'] for r in rows},training_years=tuple(range(2008,2026)),
        training_repositories=set(),allow_test=False)
    positives=defaultdict(set)
    for row in b.rows(base/'merged_ab_history_2024_2026_v6/verified_outcomes.jsonl')+rows:
        positives[row['github_numeric_id']].add(row['issue_key'])
    for row in rows:
        root=b.ROOT/row.get('evidence_source_root',row['source_root'])
        envelope=checked(root,row['evidence_ref'])
        assert b.digest(b.canonical(envelope['payload']))==envelope['payload_sha256']
        pr=find_pr(envelope['payload'],row['pr_url']);assert pr
        policy='quarter' if row['profile_as_of']==b.iso(b.quarter(pr['createdAt'])) else 'pre_interaction'
        seed=dict(url=row['pr_url'],repository=row['pr_repository'],
            author=dict(databaseId=row['github_numeric_id']),seed_identity=row['seed_identity'])
        accepted,_=t.outcome_rows(pr,seed,row['evidence_ref'],dict(targets,profile_cutoff_policy=policy),set())
        expected=next(r for r in accepted if r['outcome_id']==row['outcome_id'])
        for key in ('github_numeric_id','profile_as_of','interaction_at','outcome_at','issue_key','issue_content'):
            assert expected[key]==row[key],key
        assert not row['training_ready']
    tables={}
    def table(root,name):
        key=(root,name)
        if key not in tables:
            assert b.digest((root/name).read_bytes())==manifest(root)['files_sha256'][name]
            tables[key]=b.rows(root/name)
        return tables[key]
    pairs=b.rows(out/'comparison_index.jsonl')
    for pair in pairs:
        row=index[pair['outcome_id']];identity=row['github_numeric_id'];cutoff=b.dt(row['profile_as_of'])
        assert pair['github_numeric_id']==identity and pair['as_of']==row['profile_as_of']
        root=b.ROOT/pair['history_source_root']
        code=list(table(root,'historical_contributions.jsonl'))
        for origin,repo,commit,path in pair.get('reused_contribution_keys',[]):
            source=b.ROOT/origin;name='file_contributions.jsonl' if (source/'file_contributions.jsonl').exists() else 'historical_contributions.jsonl'
            code.extend(c for c in table(source,name) if (c['repository'],c['commit_sha'],c['path'])==(repo,commit,path))
        assert any(c['github_numeric_id']==identity and b.dt(c['event_at'])<cutoff
            and (not c.get('author_at') or b.dt(c['author_at'])<cutoff) for c in code)
        controls={(c['issue_key'],c['as_of']):c for c in table(root,'historical_controls.jsonl')}
        assert pair['comparison_issue_keys']
        for key in pair['comparison_issue_keys']:
            c=controls[key,pair['as_of']];assert c['repository']==row['repository'] and key not in positives[identity]
            payload=checked(root,c['evidence_ref'])['payload']
            issue=next(n for value in payload['data'].values() for n in value.get('nodes',[]) if n and n['url']==c['source_url'])
            replay,reason=b.issue_at(issue,cutoff)
            assert not reason and replay['content_sha256']==c['content_sha256']
    summary=b.read(out/'summary.json');assert summary['new_outcomes_with_history_and_controls']==len(pairs)
    print(json.dumps(dict(verified_outcomes=len(rows),verified_history_control_joins=len(pairs),
        source_manifests='passed',evidence_replay='passed',training_ready=False)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--version',type=int,default=4)
    main(parser.parse_args().version)
