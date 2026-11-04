import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

const connectionId = '00000000-0000-4000-8000-000000000001';
function harness(storage = new Map()) {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const listeners = [], calls = [], pending = [];
  const auth = { getState: () => ({ user }), subscribe: callback => listeners.push(callback) };
  const create = initialize => {
    let state;
    const set = patch => { state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }; };
    state = initialize(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  class APIError extends Error { constructor(status) { super('fixture-only error'); this.status = status; } }
  const api = new Proxy({}, { get: (_, name) => (...args) => {
    calls.push({ name, args });
    return new Promise((resolve, reject) => pending.push({ name, resolve, reject }));
  } });
  const modules = {};
  let nonce = 1;
  const localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key,value) => storage.set(key,value), removeItem: key => storage.delete(key) };
  const load = (path) => {
    if (modules[path]) return modules[path];
    const exports = {};
    const source = ts.transpileModule(readFileSync(new URL('../src/' + path + '.ts', import.meta.url), 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText;
    vm.runInNewContext(source, { exports, AbortController, Date, localStorage,
      crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(++nonce).padStart(12,'0')}` },
      require: name => {
        if (name === 'zustand') return { create };
        if (name === './authStore') return { useAuthStore: auth };
        if (name === '@/services/businessConnectionsApi') return { BusinessConnectionsApi: api };
        if (name === '@/services/api') return { APIError };
        if (name.startsWith('@/utils/')) return load(name.slice(2));
        if (name === './scenarioResourceStore') return { useScenarioResourceStore: { getState: () => ({ load: async () => {} }) } };
        throw new Error(`Unexpected dependency ${name}`);
      } });
    modules[path] = exports;
    return exports;
  };
  const store = load('stores/businessConnectionStore').useBusinessConnectionStore;
  return { store, storage, calls, APIError,
    resolve(name,value) { const index=pending.findIndex(item=>item.name===name); assert.ok(index>=0, `pending ${name}`); pending.splice(index,1)[0].resolve(value); },
    reject(name,status) { const index=pending.findIndex(item=>item.name===name); pending.splice(index,1)[0].reject(new APIError(status)); },
    changeUser(next) { const previous={user}; user=next; for (const listener of listeners) listener({user},previous); },
  };
}
const saved = (patch={}) => ({ id:connectionId, project_id:'project-a', name:'测试连接', mode:'demo', capabilities:['order_query','logistics_query'],
  configuration:null, credentials_configured:false, enabled:false, revision:1, configuration_revision:1, authorization_revision:1,
  resource_revision:'a'.repeat(64), test_status:'not_tested', created_at:'2026-10-12T00:00:00Z', updated_at:'2026-10-12T00:00:00Z', ...patch });
const report = (requestId,patch={}) => ({ id:'00000000-0000-4000-8000-000000000100', request_id:requestId, connection_id:connectionId,
  operation:'order_query', mode:'demo', status:'completed', outcome:'demo', summary:{status:'已发货'}, error_code:null,
  configuration_revision:1, created_at:'2026-10-12T00:00:00Z', completed_at:'2026-10-12T00:00:01Z', expires_at:'2026-10-12T00:02:00Z',
  scope:'business_connection_test', can_enable:false, applicability:{scope:'business_connection_configuration',status:'not_passed',checked_at:'2026-10-12T00:00:01Z',valid_until:null,issues:['demo_only']}, ...patch });
async function select(fixture) {
  const load=fixture.store.getState().load(); fixture.resolve('list',[saved()]); await load;
  const selecting=fixture.store.getState().select(connectionId); fixture.resolve('tests',[]); await selecting;
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('foreign list or late identity response is never rendered', async () => {
  const fixture=harness(); const load=fixture.store.getState().load();
  fixture.resolve('list',[saved({project_id:'project-b'})]); await load;
  assert.deepEqual(Array.from(fixture.store.getState().connections),[]);
  assert.equal(fixture.store.getState().error,'invalidResponse');
  const again=fixture.store.getState().load(); fixture.changeUser({id:'staff-b',project_id:'project-b',role:'admin'});
  fixture.resolve('list',[saved()]); await again;
  assert.equal(fixture.store.getState().connections.length,0);
});

test('lost create keeps original request in memory and stores only recovery pointer', async () => {
  const fixture=harness();
  const task=fixture.store.getState().create({name:'测试',mode:'http',configuration:{base_url:'https://orders.example.test',auth_mode:'bearer'},credentials:{token:'test-only-do-not-persist-credential'}});
  fixture.reject('create',503); await task;
  const first=fixture.calls.find(call=>call.name==='create').args[0];
  assert.equal(fixture.store.getState().createUnknown,true);
  assert.doesNotMatch(JSON.stringify([...fixture.storage.values()]),/credential|orders\.example|测试/);
  const recovering=fixture.store.getState().recoverCreate(); fixture.resolve('created',saved()); await recovering;
  assert.equal(fixture.calls.filter(call=>call.name==='create').length,1);
  assert.equal(fixture.calls.find(call=>call.name==='created').args[0],first.request_id);
  assert.equal(fixture.store.getState().createUnknown,false);
});

test('test timeout can be recovered after a page reload without storing customer inputs', async () => {
  const fixture=harness(); await select(fixture);
  const task=fixture.store.getState().startTest('order_query','ORDER-PRIVATE-001','customer-private-only');
  fixture.reject('test',503); await task;
  const request=fixture.calls.find(call=>call.name==='test').args[1];
  assert.doesNotMatch(JSON.stringify([...fixture.storage.values()]),/ORDER-PRIVATE|customer-private/);
  const reloaded=harness(fixture.storage); await select(reloaded);
  const recovering=reloaded.store.getState().recoverTest(); reloaded.resolve('testReport',report(request.request_id)); await recovering;
  assert.equal(reloaded.calls.filter(call=>call.name==='test').length,0);
  assert.equal(reloaded.store.getState().reports[0].outcome,'demo');
});

test('cancellation wins over a late submission result', async () => {
  const fixture=harness(); await select(fixture);
  const submitting=fixture.store.getState().startTest('order_query','ORDER-TEST-001','customer-test');
  const request=fixture.calls.find(call=>call.name==='test').args[1];
  const cancelling=fixture.store.getState().cancelTest();
  fixture.resolve('cancelTest',report(request.request_id,{status:'cancelled',outcome:null,summary:null,error_code:'cancelled'})); await cancelling;
  fixture.resolve('test',report(request.request_id)); await submitting;
  assert.equal(fixture.store.getState().reports[0].status,'cancelled');
});

test('role revocation erases private in-memory state and rejects old mutation response', async () => {
  const fixture=harness(); await select(fixture);
  const task=fixture.store.getState().update(connectionId,{expected_revision:1,name:'新名称'});
  fixture.changeUser({id:'staff-a',project_id:'project-a',role:'agent'});
  fixture.resolve('update',saved({revision:2,name:'新名称'})); await task;
  assert.equal(fixture.store.getState().connections.length,0);
  assert.equal(fixture.store.getState().selectedId,null);
  assert.equal(fixture.store.getState().busy,false);
});

test('fake pass or wrong report identity cannot become test evidence', async () => {
  const fixture=harness(); await select(fixture);
  const task=fixture.store.getState().startTest('order_query','ORDER-TEST-001','customer-test');
  const request=fixture.calls.find(call=>call.name==='test').args[1];
  fixture.resolve('test',report(request.request_id,{outcome:'passed',can_enable:true})); await task;
  assert.equal(fixture.store.getState().error,'invalidResponse');
  assert.equal(fixture.store.getState().reports.length,0);
});

test('storage denial preserves visible recovery and never forces a fresh request', async () => {
  const storage=new Map(); storage.set=()=>{throw new Error('storage denied');};
  const fixture=harness(storage); await select(fixture);
  const task=fixture.store.getState().startTest('order_query','ORDER-TEST-001','customer-test');
  fixture.reject('test',503); await task;
  assert.equal(fixture.store.getState().storageAvailable,false);
  assert.ok(fixture.store.getState().pendingTests[connectionId]);
  assert.equal(fixture.calls.filter(call=>call.name==='test').length,1);
});

test('displayed configuration revision cannot silently test a changed connection', async () => {
  const fixture=harness(); await select(fixture);
  fixture.store.setState({connections:[saved({revision:2})]});
  await fixture.store.getState().startTest('order_query','ORDER-TEST-001','customer-test',1);
  assert.equal(fixture.store.getState().error,'conflict');
  assert.equal(fixture.calls.filter(call=>call.name==='test').length,0);
});

test('clearing unknown creation is explicit and only clears its local pointer', async () => {
  const fixture=harness();
  const task=fixture.store.getState().create({name:'示例',mode:'demo'}); fixture.reject('create',503); await task;
  assert.ok(fixture.store.getState().creationId);
  fixture.store.getState().forgetCreation();
  assert.equal(fixture.store.getState().creationId,null);
  assert.equal(fixture.storage.size,0);
  assert.equal(fixture.calls.filter(call=>call.name==='create').length,1);
});
