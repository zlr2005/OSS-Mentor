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
  const cards = [];
  const trackButtons = ['all', 'newcomer', 'growth'].map(track => ({
    dataset: {track},
    attributes: {},
    handlers: {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] ?? 'false'; },
    addEventListener(name, callback) { this.handlers[name] = callback; },
    click() { this.handlers.click?.(); },
  }));
  const viewButtons = ['issues', 'repositories'].map(view => ({
    dataset: {recView: view},
    attributes: {},
    handlers: {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] ?? 'false'; },
    addEventListener(name, callback) { this.handlers[name] = callback; },
    click() { this.handlers.click?.(); },
  }));
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
      if (url.includes('/me/project-recommendations')) {
        return {items: [{repository_full_name: 'sample/project'}], ranking_source: 'test'};
      }
      if (!url.includes('/me/recommendations')) return {items: []};
      if (statusCode >= 400) throw Object.assign(new Error(body.error?.message || 'unavailable'), {status: statusCode, code: body.error?.code});
      return body;
    },
    storeBatch() {}, trackLabel: v => v || '', skeleton: () => 'loading',
    runContext: () => '',
    panel: (code,title,copy) => `${code} ${title} ${copy}`,
    issueCard: (item, index, options) => {
      cards.push(options);
      return '<article>Task</article>';
    },
    projectCard: () => '<article>Project</article>',
  };
  const context = vm.createContext({
    document: {body: {dataset: {page:'recommendations'}}, getElementById: node,
      querySelectorAll: selector => {
        if (selector === '.seg-btn') return trackButtons;
        if (selector === '[data-rec-view]') return viewButtons;
        return [];
      }, addEventListener() {}},
    window: {OMUI: ui, location: {pathname:'/recommendations',search:'',origin:'http://localhost'}},
    URL, URLSearchParams, setTimeout: (callback,delay) => {timers.push({callback,delay});return timers.length;},
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  return {nodes,context,timers,cards,trackButtons,viewButtons};
}

test('track switching hides growth project modules for newcomer track', async () => {
  const {nodes,trackButtons,viewButtons} = await page({
    profile: {},
    run_id: 'run-test',
    items: [
      {task_candidate_id: 1, service_track: 'newcomer'},
      {task_candidate_id: 2, service_track: 'growth'},
    ],
  });
  viewButtons.find(button => button.dataset.recView === 'repositories').click();
  assert.equal(nodes.get('project-opportunities').hidden, false);

  trackButtons.find(button => button.dataset.track === 'newcomer').click();
  assert.equal(nodes.get('project-opportunities').hidden, true);
});

test('repository view replaces the issue list with repository modules', async () => {
  const {nodes,viewButtons} = await page({
    profile: {},
    run_id: 'run-test',
    items: [{task_candidate_id: 2, service_track: 'growth'}],
  });
  assert.equal(nodes.get('issue-list').hidden, false);
  assert.equal(nodes.get('project-opportunities').hidden, true);

  viewButtons.find(button => button.dataset.recView === 'repositories').click();
  assert.equal(nodes.get('issue-list').hidden, true);
  assert.equal(nodes.get('project-opportunities').hidden, false);

  viewButtons.find(button => button.dataset.recView === 'issues').click();
  assert.equal(nodes.get('issue-list').hidden, false);
  assert.equal(nodes.get('project-opportunities').hidden, true);
});

test('repository cards use the same issue-card structure', async () => {
  const {context} = await page({items: []});
  vm.runInContext(shared, context);
  const card = vm.runInContext(`window.OMUI.projectCard({
    repository_full_name: 'python/mypy',
    repository_url: 'https://github.com/python/mypy',
    ranking_source: 'issue_aggregation_compat_v0.1',
    score: 0.16,
    basis: ['本次推荐中该仓库有 1 个任务命中'],
    related_issues: [{task_candidate_id: 1, issue_number: 20799, score: 0.16}]
  }, 0)`, context);

  assert.match(card, /class="issue is-top"/);
  assert.match(card, /聚合分/);
  assert.match(card, /关联 Issue/);
  assert.match(card, /仓库与贡献入口/);
});

test('empty newcomer track keeps the track selector visible', async () => {
  const {nodes,trackButtons} = await page({
    profile: {},
    items: [{task_candidate_id: 2, service_track: 'growth'}],
  });

  trackButtons.find(button => button.dataset.track === 'newcomer').click();

  assert.equal(nodes.get('home-toolbar').hidden, false);
  assert.match(nodes.get('issue-list').innerHTML, /当前筛选没有结果/);
});

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

test('unconfirmed profile is routed back to questionnaire confirmation', async () => {
  const {nodes} = await page({error:{code:'profile_not_confirmed',message:'confirm first'}},404);
  assert.match(nodes.get('issue-list').innerHTML,/完成问卷确认/);
  assert.match(nodes.get('issue-list').innerHTML,/profile_not_confirmed/);
  assert.doesNotMatch(nodes.get('issue-list').innerHTML,/推荐暂时不可用/);
});

test('context reasons show no invented points but keep negative feedback', async () => {
  const {context} = await page({items:[]});
  vm.runInContext(shared,context);
  const reason = vm.runInContext('window.OMUI.reasonList([{score_delta:0,label:"技能覆盖"}])',context);
  assert.match(reason,/匹配依据/);
  assert.doesNotMatch(reason,/\+0 点/);
  assert.match(vm.runInContext('window.OMUI.reasonList([{score_delta:-.25}])',context),/-25 点/);
});

test('cards preserve recommendation run and warnings for feedback/detail tracing', async () => {
  const {context} = await page({items:[]});
  vm.runInContext(shared,context);
  const card = vm.runInContext(`window.OMUI.issueCard({
    task_candidate_id: 7, repository_full_name: 'pytest-dev/pytest',
    issue_number: 10605, title: 'Task', service_track: 'growth', score: .5,
    difficulty: {code: 1, setup: 1}, verified_at: '2026-11-09T00:00:00Z',
    warnings: ['gated_scores_are_not_success_probabilities'],
    matched_skills: [], missing_skills: [], reasons: [],
    html_url: 'https://github.com/pytest-dev/pytest/issues/10605'
  }, 1, {feedbackContext: 'user:1:profile:p', runId: 'run-123', model: null})`, context);
  assert.match(card,/data-run-id="run-123"/);
  assert.match(card,/run_id=run-123/);
  assert.match(card,/排序分不代表完成任务的概率/);
});

test('feedback request carries the recommendation run id when present', async () => {
  assert.match(shared,/body\.run_id = runId/);
  assert.match(shared,/card\.dataset\.runId/);
});

test('initial recommendations stay visible while model prepares and automatically retry', async () => {
  const {nodes,timers} = await page({profile:{},items:[{service_track:'newcomer'}],warnings:['model_features_preparing']});
  assert.match(nodes.get('issue-list').innerHTML,/Task/);
  assert.match(nodes.get('recommendation-message').innerHTML,/个性化准备中/);
  assert.equal(timers.length,1);
  assert.equal(timers[0].delay,5000);
});

test('gated ranking metadata is available when the batch has no top-level model', async () => {
  const {cards} = await page({
    profile: {},
    items: [{service_track: 'newcomer'}],
    rankings: [{scoring_engine: 'coder_gated', model_version: 'gated-v1'}],
  });
  assert.equal(cards[0].model.variant, 'coder_gated');
  assert.equal(cards[0].model.version, 'gated-v1');
});
