"""Offline merge of a saved verification checkpoint and fresh evidence updates."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def assemble(directory):
    evidence=json.loads(gzip.decompress((directory/'first_wave_evidence.json.gz').read_bytes()))
    for path in sorted(directory.glob('evidence_update_*.json.gz')):
        update=json.loads(gzip.decompress(path.read_bytes()))
        for field in ('details','failures','timelines','pull_requests','repositories'):
            evidence[field].update(update.get(field,{}))
        for key, value in update.get('unchanged_payload_observations',{}).items():
            if key not in evidence['details'] or value.get('payload_unchanged_checked') is not True:
                raise ValueError('Unproven observation timestamp update')
            evidence['details'][key].update({k:value[k] for k in ('observed_at','method','url')})
            if value.get('projected_rejection'):
                evidence['details'][key]['projected_rejection']=value['projected_rejection']
            else:
                evidence['details'][key].pop('projected_rejection',None)
            if 'restored_body' in value:
                evidence['details'][key]['payload']['body']=value['restored_body']
                evidence['details'][key]['payload'].pop('body_omitted',None)
        evidence['listing_audit'].extend(update.get('listing_audit',[]))
        evidence['observed_end']=update['observed_end']
        evidence['inventory_coverage']=update.get('inventory_coverage',evidence.get('inventory_coverage',{}))
    raw=json.dumps(evidence,ensure_ascii=False).encode('utf-8')
    with (directory/'connector_evidence.json.gz').open('xb') as stream:
        stream.write(gzip.compress(raw))
    return dict(details=len(evidence['details']),timelines=len(evidence['timelines']),
                referenced_prs=len(evidence['pull_requests']),sha256=hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    print(json.dumps(assemble(p.parse_args().directory)))
