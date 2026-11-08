"""从 v5 已选素材提取缓存 patch；不读取 test 或外部评价，不认定历史公开性。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(document, root):
    evidence = root / document['evidence_path']
    content = root / document['content_path']
    if digest(content) != document['content_sha256']:
        raise ValueError('snapshot_hash_mismatch')
    payload = json.loads(evidence.read_text(encoding='utf-8-sig')).get('payload', {})
    matches = [f for f in payload.get('files', []) if f.get('filename') == document['path']]
    if len(matches) != 1:
        raise ValueError('missing_or_ambiguous_file')
    raw = content.read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != matches[0].get('sha'):
        raise ValueError('git_blob_mismatch')
    patch = matches[0].get('patch')
    if not isinstance(patch, str) or not patch.strip():
        if matches[0].get('changes') == 0:
            raise ValueError('zero_change_without_patch')
        raise ValueError('missing_patch')
    return dict(path=document['path'], patch=patch, commit_sha=payload.get('sha'),
                evidence_path=str(evidence), evidence_sha256=digest(evidence),
                snapshot_sha256=document['content_sha256'],
                historical_publication_status='unverified',
                interpretation='API patch may be truncated; context lines are not authored lines')


def run(root, semantics, output):
    documents = json.loads((semantics / 'documents.json').read_text())
    profiles = json.loads((semantics / 'profiles.json').read_text())
    if any(p['split'] not in ('train', 'validation') for p in profiles):
        raise ValueError('Unexpected evaluation split')
    output.mkdir(parents=True, exist_ok=False)
    patches, gaps, reasons = {}, [], Counter()
    for key, doc in documents.items():
        try:
            patches[key] = extract(doc, root)
        except (ValueError, OSError, KeyError) as error:
            reason = str(error) if isinstance(error, ValueError) else type(error).__name__
            reasons[reason] += 1
            gaps.append(dict(document_key=key, path=doc['path'], reason=reason,
                             evidence_path=doc['evidence_path'], content_path=doc['content_path']))
    coverage = {}
    for split in ('train', 'validation'):
        group = [p for p in profiles if p['split'] == split]
        counts = [sum(k in patches for k in p['documents']) for p in group]
        coverage[split] = dict(profiles=len(group), any_patch=sum(n > 0 for n in counts),
                               all_selected_patches=sum(bool(p['documents']) and n == len(p['documents'])
                                                        for p, n in zip(group, counts)))
    summary = dict(documents=len(documents), reusable_cached_patches=len(patches),
                   gaps=len(gaps), gap_reasons=dict(reasons), profile_coverage=coverage,
                   publication_verified=False, training_ready=False,
                   external_evaluation_read=False, test_read=False,
                   source_hashes={n: digest(semantics / n) for n in ('documents.json', 'profiles.json')})
    for name, value in [('patches.json', patches), ('collection_gaps.json', gaps), ('summary.json', summary)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'semantics', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.root.resolve(), args.semantics.resolve(), args.output.resolve())
