import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

test('built upload CLI binds bytes to its configured visitor and masks late responses',async()=>{
  const repo=resolve(dirname(fileURLToPath(import.meta.url)),'..'),parent=resolve(repo,'../../.tmp');
  const directory=mkdtempSync(join(parent,'visitor-upload-cli-')),config=join(directory,'config.json'),file=join(directory,'owned.txt');
  writeFileSync(file,'owned-upload-bytes');
  let mode='normal',requests=0,failure,identity;
  const save=value=>writeFileSync(config,JSON.stringify(value));
  const server=createServer(async(request,response)=>{
    try {
      requests++;
      let text='';for await(const chunk of request)text+=chunk;
      assert.equal(request.url,'/v1/chat/upload');
      assert.equal(request.headers.authorization,'Bearer private-owned-upload-grant');
      assert.ok(request.headers['content-type'].startsWith('multipart/form-data;'));
      assert.equal(text.includes('private-owned-upload-grant'),false);
      assert.ok(text.includes('owned-upload-bytes'));
      assert.ok(text.includes(identity.channel_id));
      response.setHeader('Content-Type','application/json');
      if(mode==='changed')save({...identity,visitor_id:'replacement',im_token:'replacement-grant'});
      if(mode==='echo'){response.statusCode=401;response.end(JSON.stringify({error:{message:'private-owned-upload-grant'}}));}
      else response.end(JSON.stringify({file_id:'owned-file-result',channel_id:identity.channel_id,channel_type:251}));
    }catch(error){failure=error;response.statusCode=500;response.end('{}');}
  });
  await new Promise(done=>server.listen(0,'127.0.0.1',done));
  const url=`http://127.0.0.1:${server.address().port}`;
  identity={server:url,api_key:'public-key',visitor_id:'owned-visitor',im_token:'private-owned-upload-grant',channel_id:'owned-visitor-vtr',channel_type:251};
  const run=(flags=[])=>new Promise((done,reject)=>{
    const child=spawn(process.execPath,[join(repo,'dist/index.js'),...flags,'chat','upload',file],{
      cwd:repo,windowsHide:true,env:{...process.env,TGO_WIDGET_CONFIG_PATH:config,TGO_WIDGET_SERVER:'',TGO_WIDGET_API_KEY:''}});
    let output='';const timer=setTimeout(()=>{child.kill();reject(new Error('Owned upload CLI timed out'));},20000);
    child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>output+=value);
    child.on('error',error=>{clearTimeout(timer);reject(error);});
    child.on('close',code=>{clearTimeout(timer);done({code,output});});
  });
  try {
    for(const next of ['normal','changed','echo']){
      mode=next;save(identity);const result=await run();
      assert.equal(result.output.includes('private-owned-upload-grant'),false);
      if(mode==='normal'){assert.equal(result.code,0);assert.ok(result.output.includes('owned-file-result'));}
      else{assert.notEqual(result.code,0);assert.equal(result.output.includes('owned-file-result'),false);}
    }
    const count=requests;save(identity);
    assert.notEqual((await run(['--server',url+'/other'])).code,0);
    save({...identity,im_token:undefined});assert.notEqual((await run()).code,0);
    assert.equal(requests,count);assert.ifError(failure);
  } finally {
    await new Promise(done=>server.close(done));
    const inside=relative(parent,resolve(directory));assert.ok(inside&&!inside.startsWith('..')&&!isAbsolute(inside));
    rmSync(directory,{recursive:true,force:true});
  }
});
