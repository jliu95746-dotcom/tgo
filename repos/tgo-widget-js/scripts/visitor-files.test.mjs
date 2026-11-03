import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';

const id = '11111111-1111-4111-8111-111111111111';
const options = {apiBase:'https://owned.test/api',platformApiKey:'public-key',channelId:'visitor-vtr',channelType:251,file:{}};
const messageUrl = `https://untrusted.test/v1/chat/files/${id}?file_token=old`;
function fixture(mode) {
  let cache = {visitor_id:'visitor',channel_id:'visitor-vtr',im_token:'private-original-grant'};
  const calls = [];
  const link = `${options.apiBase}/v1/chat/files/${id}?file_token=owned-file-only`;
  function load(name) {
    const exports = {};
    class XHR {
      upload = {}; status = 200;
      open(method,url) {this.headers={};calls.push({method,url,headers:this.headers});}
      setRequestHeader(key,value) {this.headers[key]=value;}
      send(body) {
        calls.at(-1).body=body;
        this.response={file_id:id,file_url:link,channel_id:options.channelId,channel_type:251};
        if(mode==='stale') cache={...cache,visitor_id:'replacement'};
        if(mode==='echo') this.response.file_url=cache.im_token;
        queueMicrotask(()=>this.onload());
      }
      abort() {this.onabort?.();}
    }
    class Form {values=[];append(...values){this.values.push(values);}}
    vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/'+name,import.meta.url),'utf8'),
      {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText, {
      exports,URL,AbortController,DOMException,setTimeout,clearTimeout,Date,queueMicrotask,XMLHttpRequest:XHR,FormData:Form,
      window:{location:{origin:'https://host.test'}},
      require:key=>key==='./visitor'?{loadCachedVisitor:()=>cache}:key==='./visitorDataAccess'?load('visitorDataAccess.ts'):{resolveApiKey:()=> 'public-key'},
      fetch:async(url,init)=>{
        calls.push({url,...init});
        return {ok:true,json:async()=>{
          if(mode==='stale') cache={...cache,visitor_id:'replacement'};
          return {access_url:mode==='evil'?'https://untrusted.test/file':mode==='echo'?cache.im_token:link,
            expires_at:new Date(Date.now()+60000).toISOString()};
        }};
      },
    });
    return exports;
  }
  return {file:load('chatFileAccess.ts'),upload:load('upload.ts'),calls,clear:()=>{cache=null;}};
}

test('web file links are renewed at the configured API with header-only original authority',async()=>{
  const f=fixture(); const link=await f.file.authorizeChatFileUrl(messageUrl,options.apiBase,'public-key');
  assert.equal(new URL(link).origin,'https://owned.test');
  assert.equal(new URL(link).searchParams.get('file_token'),'owned-file-only');
  assert.equal(new URL(link).searchParams.has('platform_api_key'),false);
  assert.equal(f.calls[0].headers.Authorization,'Bearer private-original-grant');
  assert.equal(f.calls[0].url.includes('private-original-grant'),false);
  assert.equal(f.calls[0].url.includes('untrusted.test'),false);
  const markdown=await f.file.authorizeChatMarkdown(`![图](${messageUrl})`,options.apiBase,'public-key');
  assert.ok(markdown.includes('owned-file-only'));
  assert.equal(markdown.includes('private-original-grant'),false);
});

for(const mode of ['stale','echo','evil']) test(`web file rejects ${mode} response without returning a grant`,async()=>{
  const f=fixture(mode);
  await assert.rejects(f.file.authorizeChatFileUrl(messageUrl,options.apiBase,'public-key'),error=>!error.message.includes('private-original-grant'));
});

test('web upload uses header-only authority and refuses missing or stale sessions',async()=>{
  const f=fixture(); await f.upload.uploadChatFile(options);
  assert.equal(f.calls[0].headers.Authorization,'Bearer private-original-grant');
  assert.equal(JSON.stringify(f.calls[0].body).includes('private-original-grant'),false);
  const empty=fixture();empty.clear();await assert.rejects(empty.upload.uploadChatFile(options));assert.equal(empty.calls.length,0);
  const stale=fixture('stale');await assert.rejects(stale.upload.uploadChatFile(options));
  const echo=fixture('echo');await assert.rejects(echo.upload.uploadChatFile(options),error=>!error.message.includes('private-original-grant'));
});

test('actual web file hook hides a former session link before new effects finish',async()=>{
  let revision=1,state,cleanup,deps;
  const pending=[],updates=[];
  const exports={};
  const react={useState:initial=>{
    if(state===undefined)state=initial;
    return [state,value=>{updates.push(value);state=value;}];
  },useEffect:(effect,next)=>{
    if(!deps || next.some((value,index)=>value!==deps[index])){
      cleanup?.();deps=next;cleanup=effect();
    }
  }};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/store/chatFileAccess.ts',import.meta.url),'utf8'),
    {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{
    exports,setTimeout,clearTimeout,
    require:key=>key==='react'?react:key==='./platformStore'?{usePlatformStore:selector=>selector({_apiBase:options.apiBase,_platformApiKey:'public-key'})}:
      key==='./chatStore'?{useChatStore:selector=>selector({sessionRevision:revision})}:{
        readChatFileAccess:()=>new Promise(resolve=>pending.push(resolve)),
      },
  });
  assert.equal(exports.useChatFileUrls([messageUrl])[0],'');
  pending[0]({url:'original-A-file-only'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(exports.useChatFileUrls([messageUrl])[0],'original-A-file-only');
  revision=2;
  assert.equal(exports.useChatFileUrls([messageUrl])[0],'');
  cleanup();
  const count=updates.length;
  pending[1]({url:'detached-B-file-only'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(updates.length,count);
});
