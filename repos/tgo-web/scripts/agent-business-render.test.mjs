import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const require=createRequire(import.meta.url);
function harness(){
  const user={id:'actor',project_id:'project',role:'admin'},modules={};
  const state={identity:'project:actor',agentId:'agent',pointer:null,report:null,review:null,pendingReview:null,loading:false,sending:false,reviewing:false,unknown:false,error:null,select:async()=>{},reset(){},refresh:async()=>{},start:async()=>{},submitReview:async()=>{}};
  const binding={id:'binding',connection_id:'connection',operations:['order_query'],enabled:true,revision:1};
  const current={agentId:'agent',snapshot:{active:false},loading:false,error:null};
  const summary={identity:'project:actor',entries:{agent:{data:{bindings:[binding]},ready:true,loading:false}},load:async()=>{}};
  const evidence={identity:'project:actor',entries:{agent:{data:{checked_at:new Date().toISOString(),capabilities:[{binding_id:'binding',binding_revision:1,connection_id:'connection',operation:'order_query',status:'passed',valid_until:'2099-01-01T00:00:00Z'}]},ready:true,loading:false}},load:async()=>{}};
  function load(path){if(modules[path])return modules[path];const exports={},suffix=path.includes('components/')?'.tsx':'.ts';const source=ts.transpileModule(readFileSync(new URL('../src/'+path+suffix,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
    vm.runInNewContext(source,{exports,Date,Set,Map,setInterval,clearInterval,require:name=>{
      if(['react','react/jsx-runtime','lucide-react'].includes(name))return require(name);
      if(name==='react-router-dom')return{Link:props=>React.createElement('a',{href:props.to},props.children)};
      if(name==='react-i18next')return{useTranslation:()=>({t:key=>key.replace(/^businessConnections\./,'').split('.').reduce((value,part)=>value?.[part],load('i18n/businessConnections').businessConnectionsZh)??key})};
      if(name==='@/stores/authStore')return{useAuthStore:select=>select({user})};
      if(name==='@/stores/agentBusinessCurrentStateStore')return{useAgentBusinessCurrentStateStore:()=>current};
      if(name==='@/stores/agentBusinessTrialStore')return{useAgentBusinessTrialStore:select=>select?select(state):state};
      if(name==='@/stores/agentBusinessSummaryStore')return{useAgentBusinessSummaryStore:()=>summary};
      if(name==='@/stores/agentBusinessCapabilityStore')return{useAgentBusinessCapabilityStore:()=>evidence};
      if(name.startsWith('@/'))return load(name.slice(2));if(name.startsWith('./'))return load(path.slice(0,path.lastIndexOf('/')+1)+name.slice(2));throw Error(name);
    }});modules[path]=exports;return exports;
  }
  return {state,user,evidence,current,render:(path,props)=>renderToStaticMarkup(React.createElement(load(path).default,props))};
}
test('a failed business receipt does not render a query pass or activation claim',()=>{
  const h=harness();h.state.report={id:'report',operation:'order_query',status:'completed',created_at:new Date().toISOString(),error_code:'not_found',receipt:{outcome:'failed',summary:null}};
  const html=h.render('components/business/AgentBusinessTrialPanel',{agentId:'agent',active:false,bindings:[],connections:[],disabled:false});
  assert.match(html,/查询未通过/);assert.match(html,/没有查到可核验的订单/);assert.doesNotMatch(html,/查询完成，待业务评审/);
});
test('a changed review shows retest reasons and disables approval',()=>{
  const h=harness(),state={agent_id:'agent',checked_at:new Date().toISOString(),can_review:false,valid_until:'2099-01-01T00:00:00Z',review_status:'needs_retest',issues:['configuration_changed'],reviews:[]};
  const html=h.render('components/business/AgentBusinessReviewForm',{state,disabled:false});
  assert.match(html,/员工或连接配置已变化/);assert.match(html,/disabled=""[^>]*>评审通过/);assert.match(html,/评审只覆盖这项连接和查询/);
});
test('card green represents reviewed operation only and cannot outlive its freshness',()=>{
  const h=harness(),props={agentId:'agent',onOpen(){}};
  assert.match(h.render('components/business/AgentBusinessCapabilitySummary',props),/业务评审通过/);
  h.evidence.entries.agent.data.checked_at=new Date(Date.now()-120000).toISOString();
  const stale=h.render('components/business/AgentBusinessCapabilitySummary',props);assert.match(stale,/当前能力无法核验/);assert.doesNotMatch(stale,/业务评审通过/);
  h.user.role='agent';assert.equal(h.render('components/business/AgentBusinessCapabilitySummary',props),'');
});

test('an unknown request exposes original-key cancellation and a terminal preparation explains recovery',()=>{
  const h=harness();h.state.pointer={requestId:'request',connectionId:'connection',operation:'order_query'};h.state.unknown=true;
  const props={agentId:'agent',active:false,bindings:[],connections:[],disabled:false};
  assert.match(h.render('components/business/AgentBusinessTrialPanel',props),/取消原请求/);
  h.state.unknown=false;h.state.requestState={status:'failed',error_code:'preparation_timeout'};
  const html=h.render('components/business/AgentBusinessTrialPanel',props);
  assert.match(html,/准备已超时，原请求不会继续执行/);assert.doesNotMatch(html,/取消原请求/);
});

test('review recovery shows the original decision and offers explicit continuation',()=>{
  const h=harness();h.state.pendingReview={requestId:'request',reportId:'report',command:{request_id:'request',expected_latest_review_id:null,decision:'passed',reason:null,checks:{ownership_checked:true,result_checked:true,readonly_scope_checked:true}}};
  h.state.reviewRequest={can_resume:true,processing:false,deadline_at:'2099-01-01T00:00:00Z'};
  const html=h.render('components/business/AgentBusinessReviewRecovery',{disabled:false});
  assert.match(html,/原评审决定/);assert.match(html,/继续提交原评审/);assert.match(html,/取消原评审/);assert.match(html,/沿用原版本依据/);
});

test('a processing review cannot advertise another executor or recover a missing legacy command',()=>{
  const h=harness();h.state.pendingReview={requestId:'request',reportId:'report',command:null};h.state.reviewRequest={can_resume:false,processing:true};
  const html=h.render('components/business/AgentBusinessReviewRecovery',{disabled:false});
  assert.match(html,/原评审仍在处理/);assert.match(html,/旧请求未保留原命令/);assert.doesNotMatch(html,/继续提交原评审/);assert.match(html,/取消原评审/);
});

test('test form uses current employee state even when its old parent prop differs',()=>{
  const h=harness(),props={agentId:'agent',active:true,bindings:[],connections:[],disabled:false};
  assert.doesNotMatch(h.render('components/business/AgentBusinessTrialPanel',props),/请先停用员工再开始业务测试/);
  h.current.snapshot.active=true;
  assert.match(h.render('components/business/AgentBusinessTrialPanel',{...props,active:false}),/请先停用员工/);
});
