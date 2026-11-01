"""Pinned path proofs from nonrecursive Git trees; no synthetic upstream response."""
import json
from pathlib import PurePosixPath
from urllib.parse import urlsplit


def license_name(path):
    name = PurePosixPath(path).name.casefold()
    return name.startswith(('license', 'licence', 'copying'))


def collect_paths(source, prefix, tree_sha, paths):
    evidence = {}
    payloads = {}
    entries = {}
    folders = {tree_sha:''}
    def tree(sha):
        if sha not in payloads:
            response, location = source.get(prefix+'/git/trees/'+sha)
            payload = response.payload
            if payload.get('sha') != sha or payload.get('truncated') is not False:
                raise ValueError('Nonrecursive tree incomplete or mismatched')
            if any('/' in e['path'] for e in payload['tree']):
                raise ValueError('Nonrecursive tree has nested path names')
            payloads[sha] = payload
            evidence[sha] = location
        return payloads[sha]
    root = tree(tree_sha)
    license_paths = [e['path'] for e in root['tree'] if e['type']=='blob' and license_name(e['path'])]
    for path in sorted(set(paths) | set(license_paths)):
        sha = tree_sha
        parts = PurePosixPath(path).parts
        if not parts or any(p in {'.','..','/'} for p in parts):
            raise ValueError('Invalid selected path')
        for index, part in enumerate(parts):
            entry = next((e for e in tree(sha)['tree'] if e['path']==part), None)
            if entry is None:break
            if index == len(parts)-1:
                entries[path] = {**entry, 'path':path}
            elif entry['type']=='tree':
                sha = entry['sha']
                folders[sha]='/'.join(parts[:index+1])
            else:break
    # Monorepos may keep license text in a package rather than at the root.
    # Read only bounded immediate package trees, never a recursive giant tree.
    if not license_paths:
        for entry in [e for e in root['tree'] if e['type']=='tree' and not e['path'].startswith('.')][:20]:
            folders.setdefault(entry['sha'],entry['path'])
            tree(entry['sha'])
    for sha,payload in list(payloads.items()):
        folder=folders.get(sha,'')
        for entry in payload['tree']:
            if entry['type']=='blob' and license_name(entry['path']):
                path=folder+'/'+entry['path'] if folder else entry['path']
                entries[path]={**entry,'path':path}
    license_paths=sorted(p for p in entries if license_name(p))
    proof = {'schema':'selected-git-paths-v1','sha':tree_sha,'entries':list(entries.values()),
             'tree_evidence':evidence,'license_paths':license_paths}
    return entries, proof


def verify_paths(proof, raw, expected_prefix):
    if proof.get('schema') != 'selected-git-paths-v1':
        raise ValueError('Unsupported path proof schema')
    trees = {}
    for sha, location in proof['tree_evidence'].items():
        envelope = raw(location)
        if urlsplit(envelope['metadata']['source_url']).path.casefold() != (expected_prefix+'git/trees/'+sha).casefold():
            raise ValueError('Tree evidence belongs to another repository/SHA')
        payload = envelope['payload']
        if payload.get('sha') != sha or payload.get('truncated') is not False or any('/' in e['path'] for e in payload['tree']):
            raise ValueError('Invalid nonrecursive tree evidence')
        trees[sha] = payload
    root = trees[proof['sha']]
    expected_licenses = sorted(e['path'] for e in proof['entries'] if e['type']=='blob' and license_name(e['path']))
    if expected_licenses != proof['license_paths']:
        raise ValueError('License search differs from root tree')
    seen = set()
    for claimed in proof['entries']:
        path = claimed['path'];parts = PurePosixPath(path).parts
        if path in seen or not parts or any(p in {'.','..','/'} for p in parts):
            raise ValueError('Invalid/duplicate proven path')
        seen.add(path);sha = proof['sha']
        for index, part in enumerate(parts):
            entry = next((e for e in trees[sha]['tree'] if e['path']==part), None)
            if entry is None:raise ValueError('Claimed path absent from pinned tree')
            if index==len(parts)-1:
                if {**entry,'path':path} != claimed:raise ValueError('Path proof differs from pinned entry')
            elif entry['type']=='tree':sha=entry['sha']
            else:raise ValueError('Path descends through a non-tree')
    return {'sha':proof['sha'],'tree':proof['entries'],'coverage':'selected_paths_only'}
