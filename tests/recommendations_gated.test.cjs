const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/assets/recommendations.js'), 'utf8');

async function page(body, statusCode = 200) {
  const nodes = new Map();
  const timers = [];
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      hidden: false, textContent: '', innerHTML: '', disabled: false,
      setAttribute() {}, addEventListener() {},
    });
    return nodes.get(selector);
  }
  const context = vm.createContext({
    document: {querySelector: node}, window: {location: {origin: 'http://localhost'}},
    URL, URLSearchParams,
    setTimeout: (callback, delay) => {timers.push({callback,delay});return timers.length;},
    fetch: async () => ({ok: statusCode < 400, status: statusCode, json: async () => body}),
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return {nodes, context, timers};
}

test('first-use feature state preserves profile and does not claim encoding is running', async () => {
  const {nodes} = await page({error: {code: 'model_features_required', message: 'missing'}}, 503);
  assert.equal(nodes.get('#message-title').textContent, '推荐资料需要准备');
  assert.match(nodes.get('#message-copy').textContent, /画像已保留/);
  assert.doesNotMatch(nodes.get('#message-copy').textContent, /正在生成|登录/);
  assert.equal(nodes.get('#refresh-recommendations').disabled, false);
});

test('model outage is distinct from login error', async () => {
  const {nodes} = await page({error: {code: 'model_unavailable'}}, 503);
  assert.equal(nodes.get('#message-title').textContent, '推荐模型暂不可用');
  assert.equal(nodes.get('#message-action').hidden, true);
});

test('model context reasons display without invented point contributions', async () => {
  const {context} = await page({items: []});
  const html = vm.runInContext('reasonRow({label:"技能覆盖", evidence:"Python", score_delta:0, code:"skill_match", feature_version:"v1"})', context);
  assert.match(html, /匹配依据/);
  assert.doesNotMatch(html, /\+0 点/);
  const penalty = vm.runInContext('reasonRow({score_delta:-0.25})', context);
  assert.match(penalty, /-25 点/);
});

test('initial recommendations remain visible while personalization prepares and automatically retry', async () => {
  const {nodes,timers}=await page({items:[],warnings:['model_features_preparing']});
  assert.equal(nodes.get('#context-state').textContent,'个性化准备中');
  assert.equal(timers.length,1);
  assert.equal(timers[0].delay,5000);
  assert.equal(nodes.get('#refresh-recommendations').disabled,false);
});
