const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/assets/profile.js'), 'utf8');

function harness(search = '') {
  const calls = [], stored = new Map();
  const context = vm.createContext({console, URLSearchParams, AbortController, setTimeout, clearTimeout,
    window: {location: {search}, localStorage: {
      getItem(key) { calls.push(['storage-read', key]); return stored.get(key) || null; },
      setItem(key, value) { calls.push(['storage-write', key]); stored.set(key, value); }
    }}, document: {addEventListener() {}, getElementById() {return null;}, querySelectorAll() {return [];}, querySelector() {return null;}},
    fetch: async (url, options) => {calls.push([url, options]); return {ok: true, status: 200,
      json: async () => ({api_version: 'v0.5', profile: {}, suggestions: [], consent_version: 'test-consent'})};}
  });
  vm.runInContext(source, context);
  return {context, calls, run: code => vm.runInContext(code, context)};
}

test('default GET uses authenticated API, loads suggestions and never touches local storage', async () => {
  const h = harness();
  await h.run('profileGateway.loadProfile()');
  assert.equal(h.run('state.gatewayMode'), 'api');
  assert.equal(h.run('state.persisted'), true);
  assert.equal(h.run('state.consentVersion'), 'test-consent');
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], '/api/v1/me/profile');
  assert.equal(h.calls[0][1].credentials, 'same-origin');
});

test('explicit demo is the only path to fixtures/local storage', async () => {
  const h = harness('?mode=demo');
  await h.run('profileGateway.loadProfile()');
  assert.equal(h.run('state.gatewayMode'), 'demo');
  assert.equal(h.calls.every(call => call[0] === 'storage-read'), true);
  const live = harness('?mode=unknown');
  assert.equal(live.run('state.gatewayMode'), 'api');
});

test('PUT strips identity/provenance and only sends locks for existing fields', async () => {
  const h = harness();
  await h.run(`profileGateway.saveProfile({...DEFAULT_PROFILE, user_id: 99,
    field_metadata: {'skills.Python': {locked: true}, 'skills.missing': {locked: true}, user_id: {locked: true}}})`);
  const body = JSON.parse(h.calls[0][1].body);
  assert.equal(h.calls[0][1].method, 'PUT');
  for (const key of ['user_id', 'profile_key', 'field_metadata', 'profile_source']) assert.equal(key in body, false);
  assert.deepEqual(body.locks, {'skills.Python': true});
});

test('import and both decisions use exact contract routes and bodies', async () => {
  const h = harness();
  await h.run('profileGateway.importGithub()');
  assert.deepEqual(JSON.parse(h.calls[0][1].body), {consent_version: 'profile-import-consent-v0.1'});
  for (const decision of ['accept', 'reject']) {
    await h.run(`profileGateway.resolveSuggestion({profile_field_suggestion_id: 42}, '${decision}')`);
    const [url, options] = h.calls.at(-1);
    assert.equal(url, `/api/v1/me/profile/suggestions/42/${decision}`);
    assert.equal(options.method, 'POST');
    assert.equal(options.body, '{}');
  }
  await assert.rejects(h.run("profileGateway.resolveSuggestion({profile_field_suggestion_id: -1}, 'accept')"));
});

test('404 initializes a new unsaved profile; 503/409/422/429 never fall back to demo', async () => {
  for (const status of [404, 503, 409, 422, 429]) {
    const h = harness();
    h.context.fetch = async () => ({ok: false, status, json: async () => ({error: {message: 'fixture error'}, request_id: 'req-1'})});
    if (status === 404) {
      await h.run('profileGateway.loadProfile()');
      assert.equal(h.run('state.persisted'), false);
    } else {
      await assert.rejects(h.run('profileGateway.loadProfile()'), error => error.status === status && error.message.includes('req-1'));
    }
    assert.equal(h.run('state.gatewayMode'), 'api');
    assert.equal(h.calls.length, 0);
  }
});

test('network and non-JSON failures stay errors', async () => {
  const h = harness();
  h.context.fetch = async () => {throw new TypeError('offline');};
  await assert.rejects(h.run('profileGateway.loadProfile()'), /offline/);
  h.context.fetch = async () => ({ok: true, json: async () => {throw new Error('html');}});
  await assert.rejects(h.run('profileGateway.loadProfile()'), /无效/);
  assert.equal(h.calls.length, 0);
});

test('normalization preserves zero and unrendered skills and backend import summary aliases', () => {
  const h = harness();
  assert.equal(h.run("normalizeProfile({skills: {Python: 0, docker: 2}}).skills.Python"), 0);
  assert.equal(h.run("normalizeProfile({skills: {Python: 0, docker: 2}}).skills.docker"), 2);
  assert.equal(h.run("normalizeGithubImport({recent_active_repository_count: 3}).recent_repository_count"), 3);
});

test('dirty/new/busy profiles cannot import or decide before saving', () => {
  const h = harness();
  for (const patch of ['state.ready=false', 'state.ready=true;state.busy=true',
    'state.busy=false;state.persisted=false', 'state.persisted=true;state.dirty=true']) {
    h.run(patch);
    assert.equal(h.run('canChangeServerProfile()'), false);
  }
  h.run('state.dirty=false');
  assert.equal(h.run('canChangeServerProfile()'), true);
});

test('form collection retains unknown skills and zero locks; empty choices fail validation', () => {
  const h = harness();
  const inputs = {'display-name': {value: 'Fixture'}, 'service-track': {value:'newcomer'},
    'max-code-difficulty': {value:'1'}, 'max-setup-difficulty': {value:'1'}, 'desired-skill-stretch': {value:'0'}};
  h.context.document.getElementById = id => inputs[id] || null;
  h.context.document.querySelectorAll = selector => selector === '[data-skill]'
    ? [{dataset:{skill:'Python'}, value:'0'}] : [];
  h.context.document.querySelector = () => ({checked:true});
  h.run('state.profile.skills.docker=2');
  const result = JSON.parse(h.run('JSON.stringify(collectProfileFromForm())'));
  assert.equal(result.skills.Python, 0);
  assert.equal(result.skills.docker, 2);
  assert.equal(result.field_metadata['skills.Python'].locked, true);
  assert.equal(result.preferred_languages.length, 0);
  assert.equal(h.run('validateProfile(collectProfileFromForm()).length'), 3);
});

test('401 clears account data, blocks writes and exposes login error', async () => {
  const h = harness();
  h.run('hydrateProfileForm=()=>{};renderSuggestions=()=>{};renderGithubImport=()=>{};state.ready=true;state.suggestions=[{}]');
  h.context.fetch = async () => ({ok:false, status:401, json:async()=>({error:{message:'login required'}})});
  await assert.rejects(h.run('profileGateway.loadProfile()'), error=>error.status===401);
  assert.equal(h.run('state.ready'), false);
  assert.equal(h.run('state.suggestions.length'), 0);
  assert.equal(h.run('state.profile.display_name'), '');
});

test('full evidence and 60-character name validation match backend', () => {
  const h = harness();
  assert.match(h.run("formatEvidenceItem({source:'public_path', path:'tests/test.py', repository:'org/repo'})"), /tests\/test.py.*org\/repo/);
  assert.equal(h.run("validateProfile({...DEFAULT_PROFILE,display_name:'a'.repeat(61)}).length"), 1);
});

test('save handler writes only server response and re-enables UI after success', async () => {
  const h = harness();
  h.run(`hydrateProfileForm=()=>{};updateInteractionState=()=>{};
    collectProfileFromForm=()=>({...DEFAULT_PROFILE,display_name:'Fixture'});state.ready=true;state.dirty=true`);
  h.context.event = {preventDefault(){}};
  await h.run('handleProfileSubmit(event)');
  assert.equal(h.run('state.persisted'), true);
  assert.equal(h.run('state.dirty'), false);
  assert.equal(h.run('state.busy'), false);
  assert.equal(h.calls.length, 1);
});
