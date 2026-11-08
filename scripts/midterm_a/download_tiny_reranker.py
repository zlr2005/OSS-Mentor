"""下载公开小型CrossEncoder的固定revision，记录SHA；不上传研究数据。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request


def run(output):
    os.environ.pop('SSLKEYLOGFILE',None)
    repo='cross-encoder/ms-marco-TinyBERT-L2-v2'
    with urllib.request.urlopen('https://huggingface.co/api/models/'+repo+'?blobs=true',timeout=30) as r:meta=json.load(r)
    revision=meta['sha']
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Invalid revision')
    siblings={r['rfilename']:r for r in meta['siblings']}
    names=[n for n in ['config.json','tokenizer_config.json','special_tokens_map.json','vocab.txt','tokenizer.json','model.safetensors'] if n in siblings]
    if 'model.safetensors' not in names or 'config.json' not in names:raise ValueError('Required safe files absent')
    output.mkdir(parents=True,exist_ok=False)
    (output/'remote_metadata.json').write_text(json.dumps(meta,indent=2))
    files={}
    for name in names:
        url='https://huggingface.co/'+repo+'/resolve/'+revision+'/'+name
        path=output/name;digest=hashlib.sha256();size=0
        with urllib.request.urlopen(url,timeout=60) as response,path.open('wb') as stream:
            while chunk:=response.read(1024*1024):
                size+=len(chunk)
                if size>30*1024*1024:raise ValueError('Unexpectedly large file')
                stream.write(chunk);digest.update(chunk)
        expected=(siblings[name].get('lfs') or {}).get('sha256')
        if expected and digest.hexdigest()!=expected:raise ValueError('Remote LFS SHA mismatch')
        files[name]=dict(bytes=size,sha256=digest.hexdigest(),remote_lfs_sha256=expected,url=url)
        print(json.dumps(dict(file=name,bytes=size)),flush=True)
    manifest=dict(repository=repo,revision=revision,files=files,scope='pretrained English MS MARCO passage reranker, not issue-specific training',research_data_uploaded=False)
    (output/'download_manifest.json').write_text(json.dumps(manifest,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
