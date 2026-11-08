const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/assets/recommendations.js'), 'utf8');
const shared = fs.readFileSync(path.join(__dirname, '../web/assets/ui.js'), 'utf8');

async function page(body, statusCode = 200) {
  const nodes = new Map();
  const timers = [];
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      hidden: false, textContent: '', innerHTML: '', disabled: false,
      setAttribute() {}, getAttribute() {return "false";}, removeAttribute() {}, addEventListener() {},
      querySelector() { return null; }, querySelectorAll() { return []; },
    });
    return nodes.get(id);
  }
  const ui = {
    escapeHtml: v => String(v ?? ''),
    getSession: async () => ({available: true, authenticated: true, user: {github_login: 'sample'}}),
    requestJson: async url => {
      if (!url.includes('/me/recommendations')) return {items: []};
      if (statusCode >= 400) throw Object.assign(new Error(body.error?.message || 'unavailable'), {status: statusCode, code: body.error?.code});
      return body;
    },
    storeBatch() {}, trackLabel: v => v || '', skeleton: () => 'loading',
    runContext: () => '', issueCard: () => '<article>Task</article>',
    panel: (code,title,copy) => `${code} ${title} ${copy}`,
  };
  const context = vm.createContext({
    document: {body: {dataset: {page:'recommendations'}}, getElementById: node,
      querySelectorAll: () => [], addEventListener() {}},
    window: {OMUI: ui, location: {pathname:'/recommendations',search:'',origin:'http://localhost'}},
    URL, URLSearchParams, setTimeout: (callback,delay) => {timers.push({callback,delay});return timers.length;},
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return {nodes,context,timers};
}

test('first-use feature state preserves profile without claiming encoding is running', async () => {
  const {nodes} = await page({error:{code:'model_features_required',message:'missing'}},503);
  assert.match(nodes.get('issue-list').innerHTML,/推荐资料需要准备/);
  assert.match(nodes.get('issue-list').innerHTML,/画像已保留/);
  assert.doesNotMatch(nodes.get('issue-list').innerHTML,/正在生成/);
});

test('model outage remains distinct from login error and preserves public browsing', async () => {
  const {nodes} = await page({error:{code:'model_unavailable',message:'model failure'}},503);
  assert.match(nodes.get('issue-list').innerHTML,/model_unavailable/);
  assert.match(nodes.get('issue-list').innerHTML,/推荐暂时不可用/);
  assert.match(nodes.get('session-actions').innerHTML,/退出登录/);
});

test('context reasons show no invented points but keep negative feedback', async () => {
  const {context} = await page({items:[]});
  vm.runInContext(shared,context);
  const reason = vm.runInContext('window.OMUI.reasonList([{score_delta:0,label:"技能覆盖"}])',context);
  assert.match(reason,/匹配依据/);
  assert.doesNotMatch(reason,/\+0 点/);
  assert.match(vm.runInContext('window.OMUI.reasonList([{score_delta:-.25}])',context),/-25 点/);
});

test('initial recommendations stay visible while model prepares and automatically retry', async () => {
  const {nodes,timers} = await page({profile:{},items:[{service_track:'newcomer'}],warnings:['model_features_preparing']});
  assert.match(nodes.get('issue-list').innerHTML,/Task/);
  assert.match(nodes.get('recommendation-message').innerHTML,/个性化准备中/);
  assert.equal(timers.length,1);
  assert.equal(timers[0].delay,5000);
});
