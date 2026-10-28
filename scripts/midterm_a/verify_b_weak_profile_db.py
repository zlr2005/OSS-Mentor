"""Validate an acquired database snapshot with B's adapter in its installed environment."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import sqlite3
from oss_mentor.contracts_midterm import ProfileEvidence
from oss_mentor.profiling.dataset_evidence import (
    _load_completed_dataset,adapt_a_dataset_records_to_profile_evidence,build_profile_evidence_from_a_dataset_store)

def verify(database,version):
    connection=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        manifest,records=_load_completed_dataset(connection,version)
        by_contribution=defaultdict(list);by_behavior=defaultdict(list)
        for row in records['contributions']: by_contribution[row['developer_key']].append(row)
        for row in records['project_behaviors']: by_behavior[row['developer_key']].append(row)
        repositories={r['repository_key']:r for r in records['repositories']}
        documents={r['file_version_key']:r for r in records['code_documents']}
        counts=Counter();accounts_with_weak=0
        for i,developer in enumerate(records['developers']):
            key=developer['developer_key'];cs=by_contribution[key];bs=by_behavior[key]
            repo_keys={r['repository_key'] for r in cs+bs}
            file_keys={r['file_key'] for r in cs}
            subset={**records,'contributions':cs,'project_behaviors':bs,
                    'repositories':[repositories[k] for k in sorted(repo_keys)],
                    'code_documents':[documents[k] for k in sorted({r['file_version_key'] for r in cs})],
                    'file_aliases':[r for r in records['file_aliases'] if r['file_key'] in file_keys]}
            result=adapt_a_dataset_records_to_profile_evidence(manifest,subset,developer_key=key,as_of=manifest['created_at'])
            if i<2:
                full=build_profile_evidence_from_a_dataset_store(connection,version,developer_key=key,as_of=manifest['created_at'])
                assert full==result
            assert all(isinstance(e,ProfileEvidence) for e in result['evidence'])
            if developer['identity_status']=='unconfirmed': assert not result['evidence']
            weak=[e for e in result['evidence'] if e.source_kind=='github_weak']
            accounts_with_weak+=bool(weak)
            for e in weak:
                assert e.feature_name.startswith('preference.language.') and e.details['recency_applied'] is False
                if e.details['behavior_type'] in ('fork','owned_repository'):
                    assert e.details['event_at'] is None and e.details['event_time_unknown']
            counts['weak_evidence']+=len(weak);counts['direct_evidence']+=result['coverage']['direct_evidence_count']
            for row in result['project_behavior_audit']:
                if row['behavior_type']!='contribution':
                    counts['weak_behavior_audited']+=1
                    if row['exclusion_reason']: counts['excluded_'+row['exclusion_reason']]+=1
            counts['developers_checked']+=1
            if (i+1)%100==0: print(json.dumps({'developers_checked':i+1,'weak_evidence':counts['weak_evidence']}),flush=True)
        assert counts['weak_behavior_audited']==manifest['entity_counts']['weak_interest_behaviors']
        parent=manifest['parent_dataset_version']
        for table in ('midterm_dataset_contribution','midterm_dataset_code','midterm_dataset_developer'):
            for a,b in ((version,parent),(parent,version)):
                assert not connection.execute(f'SELECT payload_json FROM {table} WHERE dataset_version=? EXCEPT SELECT payload_json FROM {table} WHERE dataset_version=?',(a,b)).fetchall()
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
        return {'status':'B_adapter_verified','dataset_version':version,'counts':dict(counts),
                'accounts_with_weak_evidence':accounts_with_weak,'grouped_and_full_store_samples_equal':True,
                'inherited_identity_code_contribution_payloads_unchanged':True}
    finally: connection.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True);parser.add_argument('--version',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=verify(args.database,args.version)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,indent=2))
