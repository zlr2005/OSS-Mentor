"""区分旧bundle完整性与图训练来源可审计性。"""
import argparse
import hashlib
import json
from pathlib import Path


def run(root,bundle,output):
    output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((bundle/'CODER_BUNDLE_MANIFEST.json').read_text())
    checks=[]
    for row in manifest['files']:
        p=bundle/row['path']
        okay=p.is_file() and p.stat().st_size==row['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256']
        checks.append(dict(path=str(p),integrity_passed=okay))
    expected=[root/'CODER/dataset.bson',root/'CODER/resolved_issue.bson',root/'data/midterm/normalized/gfibot-m0-v1.1/manifest.json',
              bundle/'data/midterm/normalized/gfibot-m0-v1.1/manifest.json']
    report=dict(bundle_version=manifest['bundle_version'],manifest_files=len(checks),passed=sum(c['integrity_passed'] for c in checks),
        file_checks=checks,checked_source_locations=[dict(path=str(p),exists=p.exists()) for p in expected],
        missing_from_bundle=['raw BSON sources','normalization/split manifest','timestamped training edges','creation-time text/label snapshots and their provenance'],
        graph_time_configuration='fixed_train_graph train_before2021, valid_before2022 is declared in config; actual edge/timestamp reconstruction not possible from weights and mappings alone',
        provenance_audit_complete=False,old_model_modified=False,
        alternative='fresh public-text model does not consume old graph, embeddings, or learned adapters; frozen external MiniLM corpus still not independently audited')
    (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='file_checks'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','bundle','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.root,a.bundle,a.output)
