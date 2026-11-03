import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const require = createRequire(import.meta.url);

function harness(overrides={}) {
  const modules={};
  const state={connections:[],selectedId:null,reports:[],pendingTests:{},loading:false,historyLoading:false,busy:false,submitting:false,cancelling:false,
    error:null,creationId:null,creationBody:null,createUnknown:false,storageAvailable:true,hasMore:false,load:async()=>{},clearError(){},...overrides};
  const user={role:'admin',project_id:'project-a',id:'staff-a'};
  const store=Object.assign(()=>state,{getState:()=>state});
  const identity={policy:null,platforms:[],bindings:[],loading:false,saving:false,error:null,unknown:false,load:async()=>{},reset(){},...overrides.identity};
  function load(path) {
    if(modules[path])return modules[path];
    const suffix=path.includes('components/')?'.tsx':'.ts';
    const exports={};
    const source=ts.transpileModule(readFileSync(new URL('../src/'+path+suffix,import.meta.url),'utf8'),{
      compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX},
    }).outputText;
    vm.runInNewContext(source,{exports,Date,URL,require:name=>{
      if(name==='react'||name==='react/jsx-runtime'||name==='lucide-react')return require(name);
      if(name==='react-i18next')return {useTranslation:()=>({t:(key)=>{
        const data=load('i18n/businessConnections').businessConnectionsZh;
        return key.replace(/^businessConnections\./,'').split('.').reduce((value,part)=>value?.[part],data)??key;
      }})};
      if(name==='@/stores/businessConnectionStore')return {useBusinessConnectionStore:store};
      if(name==='@/stores/businessCustomerIdentityStore')return {useBusinessCustomerIdentityStore:Object.assign(()=>identity,{getState:()=>identity})};
      if(name==='@/stores/authStore')return {useAuthStore:select=>select({user})};
      if(name==='@/hooks/useDialogFocus')return {useDialogFocus:()=>({current:null})};
      if(name==='@/components/ui/ConfirmDialog')return {default:()=>null};
      if(name.startsWith('@/'))return load(name.slice(2));
      if(name.startsWith('./'))return load(path.slice(0,path.lastIndexOf('/')+1)+name.slice(2));
      throw new Error(`Unexpected import ${name}`);
    }});
    modules[path]=exports;return exports;
  }
  return {state,user,render(path,props={}){return renderToStaticMarkup(React.createElement(load(path).default,props));}};
}

test('business connection page keeps administrator and example evidence boundaries',()=>{
  const fixture=harness({connections:[{id:'connection-a',name:'示例订单',mode:'demo',capabilities:['order_query'],enabled:false,test_status:'not_tested',revision:1}]});
  const html=fixture.render('components/business/connections/BusinessConnectionManagement');
  assert.match(html,/示例订单/);assert.match(html,/不代表真实订单/);assert.match(html,/disabled=""[^>]*>启用连接/);
  fixture.user.role='agent';
  assert.match(fixture.render('components/business/connections/BusinessConnectionManagement'),/仅管理员/);
});

test('unknown creation and unavailable storage remain explicit recovery states',()=>{
  const fixture=harness({creationId:'request-a',createUnknown:true,storageAvailable:false});
  const html=fixture.render('components/business/connections/BusinessConnectionManagement');
  assert.match(html,/查询原请求/);assert.match(html,/浏览器无法保存恢复编号/);
  assert.match(html,/disabled=""[^>]*>添加连接/);
});

test('historical success with stale owner assessment never renders current proof',()=>{
  const fixture=harness({selectedId:'connection-a',reports:[{id:'report-a',connection_id:'connection-a',mode:'http',operation:'order_query',status:'completed',outcome:'passed',configuration_revision:1,
    created_at:'2026-10-12T00:00:00Z',summary:{status:'已发货'},error_code:null,
    applicability:{scope:'business_connection_configuration',status:'stale',checked_at:'2026-10-12T00:00:01Z',valid_until:null,issues:['configuration_changed']}}]});
  const html=fixture.render('components/business/connections/ConnectionTestDialog',{connection:{id:'connection-a',name:'订单系统',mode:'http',capabilities:['order_query'],revision:2},onClose(){}});
  assert.match(html,/历史查询结果/);assert.match(html,/需要重测/);assert.match(html,/连接配置已改变/);
  assert.doesNotMatch(html,/>当前有效</);
});

test('customer identity settings never expose saved merchant keys or grant execution',()=>{
  const fixture=harness({identity:{policy:{signing_key_configured:true,platform_ids:['platform-a'],max_session_seconds:900,enabled:true,connection_configuration_revision:1},platforms:[{id:'platform-a',name:'网站客户'}],bindings:[]}});
  const html=fixture.render('components/business/connections/CustomerIdentityPolicyDialog',{connection:{id:'connection-a',configuration_revision:1},onClose(){}});
  assert.match(html,/客户身份验证/);assert.match(html,/原密钥不显示/);assert.match(html,/不会启用 AI 员工/);
  assert.match(html,/type="password"[^>]*value=""/);assert.match(html,/网站客户/);
});
