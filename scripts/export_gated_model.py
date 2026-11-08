"""将本机可信CODER门控研究权重导出为无pickle的只读平台模型包。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from oss_mentor.gated_scoring import FEATURE_SCHEMA,NumpyGatedModel,sha_file


def export(experiment,arm,seed,output):
    import torch
    p=json.loads((experiment/'protocol.json').read_text(encoding='utf-8'))
    if seed not in p['seeds'] or arm not in p['arms']:raise ValueError('Arm/seed absent from experiment protocol')
    if not all(sha_file(Path(k))==v for k,v in p['input_sha256'].items()):raise ValueError('Experiment dependencies changed')
    weights=experiment/f'{arm}-{seed}/weights.pt';normalizer=experiment/f'{arm}-{seed}/normalizer.json'
    state=torch.load(weights,weights_only=True,map_location='cpu');n=json.loads(normalizer.read_text())
    expected_dim=state['pair.net.0.weight'].shape[1]
    if expected_dim!=16 or len(n['mean'])!=16:raise ValueError('Exporter supports the platform128+16 schema only')
    data={k:v.detach().cpu().numpy().astype(np.float32) for k,v in state.items()}
    data.update(pair_mean=np.asarray(n['mean'],dtype=np.float32),pair_std=np.asarray(n['std'],dtype=np.float32))
    dependencies={k:v for k,v in p['input_sha256'].items() if any(f'{a}-{seed}' in k for a in ['original','learned_behavior','cosine']) or Path(k).name in ['historical_issue_embeddings.npz','model_manifest.json','embeddings.npz']}
    if not dependencies:raise ValueError('Missing backbone provenance')
    backbone=hashlib.sha256(json.dumps(dependencies,sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True,exist_ok=False);np.savez_compressed(output/'weights.npz',**data)
    manifest=dict(format='coder-gated-numpy-v1',feature_schema=FEATURE_SCHEMA,model_version=f'coder-gated-{arm}-s{seed}',
        dataset_version='public-text-supervision-20261107-v1',graph_version='gfibot-m0-v1.1-fixed-train-graph',
        encoder_version='all-MiniLM-L6-v2@c21050a7ef692090620a6d037dd736908f9c7cf6',
        seed=seed,arm=arm,backbone_sha256=backbone,backbone_dependencies=dependencies,weights_sha256=sha_file(output/'weights.npz'),
        original_weights_sha256=sha_file(weights),normalizer_sha256=sha_file(normalizer),experiment_protocol_sha256=sha_file(experiment/'protocol.json'),
        selection='explicit seed; no automatic best-seed selection',scores_are_probabilities=False,independently_tested=False,
        inherited_audit_limit=p.get('inherited_audit_limit'),runtime_download_or_training=False)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    NumpyGatedModel(output)
    print(json.dumps(dict(bundle=str(output),seed=seed,model_version=manifest['model_version'])))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--experiment',type=Path,required=True);p.add_argument('--arm',required=True)
    p.add_argument('--seed',type=int,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();export(a.experiment,a.arm,a.seed,a.output)
