"""Build a network-free historical ranking replay from saved, verified research outputs."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def run():
    base=ROOT/'data/midterm/working'; ranked=base/'zeroshot_rerank_20261107_v1'; expanded=base/'expanded_pool_20261107_v2'
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    files=[ranked/n for n in ['protocol.json','pair_plan.json','tree_ensemble.json','summary.json']]
    files += [expanded/'protocol.json',expanded/'candidate_plans.json',base/'public_text_dataset_20261107_v2/samples.jsonl',base/'public_text_dataset_20261107_v2/candidates.jsonl',base/'public_x_profiles_20261107_v1/lineage.jsonl']
    for protocol in [read(ranked/'protocol.json'),read(expanded/'protocol.json')]:
        for p,h in protocol['input_sha256'].items():
            if sha(Path(p))!=h: raise ValueError(f'Frozen input changed: {p}')
    hashes={str(p):sha(p) for p in files}
    rows=lambda p:[json.loads(l) for l in p.read_text(encoding='utf-8').splitlines()]
    candidates={r['issue_key']:r for r in rows(base/'public_text_dataset_20261107_v2/candidates.jsonl')}
    samples={r['outcome_id']:r for r in rows(base/'public_text_dataset_20261107_v2/samples.jsonl') if r['split']=='validation'}
    lin={r['profile_version']:r for r in rows(base/'public_x_profiles_20261107_v1/lineage.jsonl')}
    plans=read(expanded/'candidate_plans.json'); top=defaultdict(list)
    for p in read(ranked/'pair_plan.json'): top[p['profile_version']].append(p['issue_key'])
    assert len(top)==159 and all(len(keys)==len(set(keys))==50 for keys in top.values())
    outcomes=defaultdict(list)
    for r in read(ranked/'tree_ensemble.json'):
        s=samples[r['outcome_id']]; v=r['profile_version']; target=s['positive_issue_key']
        assert r['pool_size']==len(plans[v]) and set(top[v])<=set(plans[v])
        assert (top[v].index(target)+1 if target in top[v] else r['rank'])==r['rank']
        assert (target in top[v])==(r['rank']<=50)
        outcomes[v].append(dict(issue=target,rank=r['rank']))
    queries=[]
    for v in sorted(top,key=lambda x:hashlib.sha256(x.encode()).hexdigest()):
        s=next(s for s in samples.values() if s['profile_version']==v)
        queries.append(dict(profile=v,as_of=s['as_of'],pool=len(plans[v]),outcomes=outcomes[v],
            history=[dict(repository=h[0],path=h[2]) for h in lin[v]['retained_X']],
            tasks=[dict(rank=i+1,issue=k,title=candidates[k]['title'],body=candidates[k]['body_text']) for i,k in enumerate(top[v])]))
    data=json.dumps(queries,ensure_ascii=False).replace('<','\\u003c')
    html=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OSS-Mentor 历史研究回放</title><style>body{font:16px system-ui;max-width:1060px;margin:36px auto;padding:0 20px;color:#192b3a;background:#f5f7fa}header,article,section{background:white;padding:24px;border-radius:12px;margin:16px 0}h1{margin:0 0 12px}.notice{background:#fff0c8;padding:16px;border-radius:8px}select{max-width:100%;padding:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px system-ui}summary{cursor:pointer}small{color:#5c6773}.tag{color:#945a00}li{margin:8px 0}</style>
<header><h1>OSS-Mentor 历史研究回放</h1><div class="notice">历史开发验证 · 离线只读 · 非当前可领取任务 · 非独立测试 · 不调用在线服务</div>
<p>冻结普通树五seed集成：MRR 0.639702，Recall@5 89.00%。这是集成结果，与单模型五seed均值0.638936、89.57%口径不同。</p>
<p>展示全部159份查询、209条outcome，按画像哈希排列；Top50顺序来自已保存的检索计划，未挑选成功案例。</p><label>选择历史查询 <select id="query"></select></label></header>
<section id="context"></section><main id="tasks"></main><footer><p>标题、正文、路径均为历史证据，仅用于研究回放。历史成功用于评价标记，不作为预测输入；排名分数不代表解决概率。</p></footer>
<script>const data=__DATA__;const select=document.getElementById('query');
function el(tag,text,parent){const e=document.createElement(tag);e.textContent=text;parent.appendChild(e);return e;}
data.forEach((q,i)=>{const o=document.createElement('option');o.value=i;o.textContent=`${i+1}. ${q.as_of} · ${q.profile}`;select.appendChild(o)});
function render(){const q=data[Number(select.value)],c=document.getElementById('context'),t=document.getElementById('tasks');c.replaceChildren();t.replaceChildren();
el('h2','截止前证据与评价目标',c);el('p',`截止 ${q.as_of} ｜ 全池 ${q.pool} 个候选 ｜ 下方回放Top50`,c);
const ul=el('ul','',c);q.outcomes.forEach(o=>el('li',`历史目标 ${o.issue}，全池名次 ${o.rank}`,ul));
const d=el('details','',c);el('summary',`历史路径证据（${q.history.length}条）`,d);el('pre',q.history.map(h=>h.repository+' / '+h.path).join('\n'),d);
q.tasks.forEach(task=>{const a=el('article','',t);el('small',`#${task.rank} · ${task.issue}`,a);el('h2',task.title,a);
if(q.outcomes.some(o=>o.issue===task.issue))el('p','历史已知成功目标（仅评价标记）',a).className='tag';
const detail=el('details','',a);el('summary','查看历史任务正文',detail);el('pre',task.body,detail);});}
select.addEventListener('change',render);render();</script></html>'''.replace('__DATA__',data)
    output=ROOT/'output/defense_replay_20261108.html'
    if output.exists(): raise FileExistsError(output)
    output.parent.mkdir(exist_ok=True);output.write_text(html,encoding='utf-8')
    manifest=dict(input_sha256=hashes,output_sha256=sha(output),queries=len(queries),outcomes=sum(map(len,outcomes.values())),
                  top50_pairs=sum(map(len,top.values())),all_saved_target_ranks_verified=True,
                  independent_test=False,live_availability_claim=False,network_required=False)
    (output.with_suffix('.manifest.json')).write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),queries=len(queries),outcomes=manifest['outcomes'],verified=True)))


if __name__=='__main__': run()
