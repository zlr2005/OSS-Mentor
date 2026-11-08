"""核对共同文件三个编码视图的字符和 token 截断比例。"""
import argparse
import json
import os
from pathlib import Path


def run(semantics, patches, assets):
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['HF_HUB_CACHE']=str(assets/'encoding-assets/hf_hub')
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained('sentence-transformers/all-MiniLM-L6-v2',revision='c21050a7ef692090620a6d037dd736908f9c7cf6',local_files_only=True)
    documents=json.loads((semantics/'documents.json').read_text())
    patch_map=json.loads((patches/'patches.json').read_text())
    stats={}
    for view in ('path','content','patch'):
        lengths=[]
        clipped=0
        for key,d in documents.items():
            text='[FILE] '+d['path']
            if view!='path':
                raw=Path(d['content_path']).read_text(encoding='utf-8',errors='replace') if view=='content' else patch_map[key]['patch']
                clipped+=len(raw)>8192
                text+='\n['+('CONTENT' if view=='content' else 'PATCH')+']\n'+raw[:8192]
            lengths.append(len(tokenizer(text,truncation=False,verbose=False)['input_ids']))
        stats[view]=dict(documents=len(lengths),character_clipped=clipped,token_clipped=sum(n>256 for n in lengths),
                         max_tokens_before_clip=max(lengths))
    (semantics/'token_budget_audit.json').write_text(json.dumps(stats,indent=2))
    print(json.dumps(stats))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('semantics','patches','assets'):
        p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    run(a.semantics,a.patches,a.assets)
