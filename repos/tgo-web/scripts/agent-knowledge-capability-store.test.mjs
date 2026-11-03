import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

function harness() {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const pending = [], calls = [], listeners = [];
  const create = initializer => {
    let state;
    const set = next => { state = { ...state, ...(typeof next === 'function' ? next(state) : next) }; };
    state = initializer(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL('../src/stores/agentKnowledgeCapabilityStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(source, { exports, AbortController, Map, Date, require: name => {
    if (name === 'zustand') return { create };
    if (name === './authStore') return { useAuthStore: { getState: () => ({ user }), subscribe: fn => listeners.push(fn) } };
    if (name === '@/services/agentKnowledgeCapabilitiesApi') return { AgentKnowledgeCapabilitiesApi: { read: (...args) => {
      calls.push(args); return new Promise((resolve,reject) => pending.push({resolve,reject}));
    } } };
    throw new Error(name);
  } });
  return { store: exports.useAgentKnowledgeCapabilityStore, calls,
    resolve: value => pending.shift().resolve(value), reject: () => pending.shift().reject(new Error('unavailable')),
    change: next => { const previous={user};user=next;listeners.forEach(fn=>fn({user},previous)); } };
}
function value() {
  const cases = ['grounded_answer', 'grounded_answer', 'safe_refusal'].map((expected,index) => ({
    case: { id:`knowledge.v1.${index+1}`,template_id:'knowledge',template_version:1,criteria_version:1,question:`问题${index}`,expected_result:expected },
    status:'not_tested', report_id:null, review_id:null, review_revision:null, valid_until:null,
  }));
  return { agent_id:'agent-a',reviewed_by:'staff-a',channel:'web',scope:'knowledge_templates',can_enable:false,enabled:false,
    knowledge_status:'available',checked_at:'2026-10-11T00:00:00Z',capabilities:[{template_id:'knowledge',template_version:1,
      status:'not_tested',answer_cases_passed:0,answer_cases_required:2,safety_cases_passed:0,safety_cases_required:1,cases,valid_until:null}] };
}

test('one shared load is cached and never changes activation', async()=>{
  const f=harness();const request=f.store.getState().load('agent-a');await f.store.getState().load('agent-a');
  assert.equal(f.calls.length,1);f.resolve(value());await request;
  await f.store.getState().load('agent-a');assert.equal(f.calls.length,1);
  const entry=f.store.getState().entries['agent-a:web'];assert.equal(entry.ready,true);assert.equal(entry.data.can_enable,false);
});

for (const available of [true, false]) {
  test(`complete current coverage ${available ? 'is informational only' : 'requires available knowledge'}`, async () => {
    const f = harness(), body = value(), group = body.capabilities[0];
    group.status = 'passed'; group.answer_cases_passed = 2; group.safety_cases_passed = 1; group.valid_until = '2099-01-01T00:00:00Z';
    group.cases.forEach((row, index) => Object.assign(row, { status: 'passed', report_id: `report-${index}`, review_id: `review-${index}`, review_revision: 1, valid_until: group.valid_until }));
    if (!available) body.knowledge_status = 'unknown';
    const request = f.store.getState().load('agent-a'); f.resolve(body); await request;
    const entry = f.store.getState().entries['agent-a:web'];
    assert.equal(entry.ready, available);
    if (available) assert.equal(entry.data.can_enable, false);
  });
}

for(const variant of ['agent','actor','channel','enable','counts','partial_pass','safety_only']) {
  test(`rejects ${variant} coverage without keeping a passed state`,async()=>{
    const f=harness();const request=f.store.getState().load('agent-a');const body=value();
    if(variant==='agent')body.agent_id='other';
    else if(variant==='actor')body.reviewed_by='other';
    else if(variant==='channel')body.channel='wecom_kf';
    else if(variant==='enable')body.can_enable=true;
    else if(variant==='counts')body.capabilities[0].answer_cases_passed=2;
    else {const group=body.capabilities[0];group.status='passed';group.valid_until='2099-01-01T00:00:00Z';
      if(variant==='safety_only'){group.cases=group.cases.slice(2);group.cases[0].status='passed';group.answer_cases_required=0;group.safety_cases_passed=1;}}
    f.resolve(body);await request;const entry=f.store.getState().entries['agent-a:web'];
    assert.equal(entry.ready,false);assert.equal(entry.error,true);assert.equal(entry.data,null);
  });
}

test('failed refresh keeps history but stops claiming it is current',async()=>{
  const f=harness();let request=f.store.getState().load('agent-a');f.resolve(value());await request;
  request=f.store.getState().load('agent-a','web',true);f.reject();await request;
  const entry=f.store.getState().entries['agent-a:web'];assert.ok(entry.data);assert.equal(entry.ready,false);assert.equal(entry.error,true);
});

test('identity change aborts and ignores old requests',async()=>{
  const f=harness();const request=f.store.getState().load('agent-a');
  f.change({id:'staff-b',project_id:'project-b',role:'admin'});assert.equal(f.calls[0][2].aborted,true);
  f.resolve(value());await request;assert.equal(Object.keys(f.store.getState().entries).length,0);
});

test('nonadmin never reads employee review evidence',async()=>{
  const f=harness();f.change({id:'staff-a',project_id:'project-a',role:'user'});
  await f.store.getState().load('agent-a');assert.equal(f.calls.length,0);
});
