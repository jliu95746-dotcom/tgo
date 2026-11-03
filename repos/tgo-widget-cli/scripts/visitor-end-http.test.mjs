import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { dirname, join, resolve, relative, isAbsolute } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

test('built CLI end removes readable credentials, retries only end grants and masks failures',async()=>{
  const repo=resolve(dirname(fileURLToPath(import.meta.url)),'..'), parent=resolve(repo,'../../.tmp')
  const directory=mkdtempSync(join(parent,'visitor-end-cli-')), config=join(directory,'config.json')
  let confirmed=false,ends=0,visitorNumber=0,expectedEndVisitor='owned-visitor-1'
  const server=createServer(async(request,response)=>{
    let text='';for await(const chunk of request) text+=chunk
    const body=JSON.parse(text || '{}')
    response.setHeader('Content-Type','application/json')
    if(request.url==='/v1/visitors/register'){
      visitorNumber++
      response.statusCode=201
      response.end(JSON.stringify({id:'owned-visitor-'+visitorNumber,platform_open_id:'owned-open',channel_id:'owned-channel',channel_type:251,
        platform_id:'owned-platform',project_id:'owned-project',im_token:'private-im-read',visitor_access_token:'private-data-read',visitor_end_token:'private-end-only'}))
    }else if(request.url==='/v1/visitors/access/end'){
      ends++
      assert.equal(body.visitor_id,expectedEndVisitor)
      assert.equal(body.visitor_end_token,'private-end-only')
      assert.equal(body.visitor_access_token,undefined)
      const stored=readFileSync(config,'utf8')
      assert.equal(stored.includes('private-data-read'),false)
      assert.equal(stored.includes('private-im-read'),false)
      if(!confirmed){response.statusCode=503;response.end(JSON.stringify({message:'raw-secret-'+body.visitor_end_token}))}
      else response.end(JSON.stringify({status:'ended',upstream_stop_status:'confirmed',im_disconnect_status:'requested'}))
    }else {response.statusCode=404;response.end('{}')}
  })
  await new Promise(done=>server.listen(0,'127.0.0.1',done))
  const run=args=>new Promise((done,reject)=>{
    const processChild=spawn(process.execPath,[join(repo,'dist/index.js'),'--server',`http://127.0.0.1:${server.address().port}`,'--api-key','public-key',...args],
      {cwd:repo,windowsHide:true,env:{...process.env,TGO_WIDGET_CONFIG_PATH:config}})
    let output='';const timeout=setTimeout(()=>{processChild.kill();reject(new Error('Owned end CLI timed out'))},20000)
    processChild.stdout.on('data',value=>output+=value);processChild.stderr.on('data',value=>output+=value)
    processChild.on('error',error=>{clearTimeout(timeout);reject(error)})
    processChild.on('close',code=>{clearTimeout(timeout);done({output,code})})
  })
  try{
    assert.equal((await run(['init'])).code,0)
    const ended=await run(['session','end'])
    assert.equal(ended.code,0)
    assert.equal(ended.output.includes('private-'),false)
    assert.equal(ended.output.includes('pending'),true)
    const state=JSON.parse(readFileSync(config,'utf8'))
    assert.equal(state.visitor_id,undefined)
    assert.equal(state.pending_visitor_ends[0].visitor_end_token,'private-end-only')
    const who=await run(['whoami']);assert.equal(who.output.includes('private-'),false)
    confirmed=true
    const retry=await run(['session','retry-end'])
    assert.equal(retry.code,0);assert.equal(retry.output.includes('private-'),false)
    assert.equal(ends,2)
    assert.equal(JSON.parse(readFileSync(config,'utf8')).pending_visitor_ends?.length || 0,0)
    assert.equal((await run(['init'])).code,0)
    expectedEndVisitor='owned-visitor-2'
    assert.equal((await run(['init'])).code,0)
    assert.equal(ends,3)
    assert.equal(JSON.parse(readFileSync(config,'utf8')).visitor_id,'owned-visitor-3')
  }finally{
    await new Promise(done=>server.close(done))
    const inside=relative(parent,resolve(directory));assert.ok(inside&&!inside.startsWith('..')&&!isAbsolute(inside))
    rmSync(directory,{recursive:true,force:true})
  }
})
