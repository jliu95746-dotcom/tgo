import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/businessConnectionForm.ts',import.meta.url),'utf8'),{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
}).outputText,{exports,URL,Intl});
const {connectionForm,connectionPayload} = exports;
test('demo preview drops all real connection and credential fields',()=>{
  const form=connectionForm(); form.name='示例'; form.mode='demo'; form.token='test-only-private';
  const payload=connectionPayload(form);
  assert.equal(payload.mode,'demo'); assert.ok(!('credentials' in payload)); assert.ok(!('configuration' in payload));
});
test('real connection needs a valid origin and matching new credentials',()=>{
  const form=connectionForm(); form.name='真实连接'; form.configuration.base_url='https://orders.example.test'; form.configuration.auth_mode='bearer';
  assert.throws(()=>connectionPayload(form),/credentials/);
  form.token='test-only-private';
  assert.equal(connectionPayload(form).credentials.token,'test-only-private');
  form.configuration.base_url='http://127.0.0.1'; assert.throws(()=>connectionPayload(form),/origin/);
});
test('editing can retain stored credentials only when explicitly chosen',()=>{
  const connection={id:'connection-a',name:'已有连接',mode:'http',capabilities:['order_query'],revision:3,
    credentials_configured:true,configuration:{base_url:'https://orders.example.test',auth_mode:'bearer'}};
  const form=connectionForm(connection);
  assert.equal(form.token,''); assert.equal(form.reuseCredentials,true);
  const retained=connectionPayload(form,connection);
  assert.ok(!('credentials' in retained)); assert.equal(retained.expected_revision,3);
  form.configuration.base_url='https://another.example.test'; form.reuseCredentials=false;
  assert.throws(()=>connectionPayload(form,connection),/credentials/);
  form.token='rotated-fixture'; assert.equal(connectionPayload(form,connection).credentials.token,'rotated-fixture');
});
test('no authentication explicitly clears old server credentials',()=>{
  const connection={id:'connection-a',name:'已有连接',mode:'http',capabilities:['order_query'],revision:3,
    credentials_configured:true,configuration:{base_url:'https://orders.example.test',auth_mode:'bearer'}};
  const form=connectionForm(connection); form.configuration.auth_mode='none';
  assert.equal(connectionPayload(form,connection).credentials,null);
});

test('merchant mapping preserves saved dictionaries and sends parsed business settings',()=>{
  const form=connectionForm({name:'商户',configuration:{base_url:'https://merchant.example.test',order_status_map:{SENT:'已发货'},not_found_values:['MISSING']}});
  assert.equal(form.orderStatusMappings,'SENT=已发货');
  form.configuration.merchant_id='shop-42'; form.configuration.success_field='code';
  form.configuration.request_order_no_field='orderNumber'; form.configuration.default_timezone='Asia/Shanghai';
  form.logisticsStatusMappings='TRANSIT=运输中\nDELIVERED=已签收'; form.notShippedCodes='PENDING, CREATED';
  const payload=connectionPayload(form);
  assert.equal(payload.configuration.request_order_no_field,'orderNumber');
  assert.equal(payload.configuration.logistics_status_map.TRANSIT,'运输中');
  assert.equal(payload.configuration.logistics_not_shipped_values.join(','),'PENDING,CREATED');
  form.configuration.request_operation_field=null;
  assert.equal(connectionPayload(form).configuration.request_operation_field,null);
});

test('ambiguous merchant settings cannot be submitted',()=>{
  const form=connectionForm(); form.name='商户'; form.configuration.base_url='https://merchant.example.test';
  form.orderStatusMappings='SENT=已发货\nSENT=重复'; assert.throws(()=>connectionPayload(form),/mapping/);
  form.orderStatusMappings=''; form.configuration.request_customer_id_field='order_no'; assert.throws(()=>connectionPayload(form),/mapping/);
  form.configuration.request_customer_id_field='customer_id'; form.notFoundCodes='0'; form.configuration.success_field='code'; assert.throws(()=>connectionPayload(form),/mapping/);
  form.notFoundCodes=''; form.logisticsStatusMappings='PENDING=运输中'; form.notShippedCodes='PENDING'; assert.throws(()=>connectionPayload(form),/mapping/);
  form.logisticsStatusMappings=''; form.notShippedCodes=''; form.configuration.default_timezone='Invalid/Timezone'; assert.throws(()=>connectionPayload(form),/mapping/);
});
