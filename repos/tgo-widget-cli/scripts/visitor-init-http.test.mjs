import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { dirname, join, relative, resolve, isAbsolute } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

test('built CLI init retains access privately and rejects old-server responses without overwriting config', async () => {
  const repo=resolve(dirname(fileURLToPath(import.meta.url)),'..')
  const parent=resolve(repo,'../../.tmp')
  const temporary=mkdtempSync(join(parent,'visitor-init-http-'))
  const config=join(temporary,'config.json')
  let missing=false
  const server=createServer(async (request,response)=>{
    assert.equal(request.url,'/v1/visitors/register')
    for await (const chunk of request) void chunk
    response.writeHead(201,{'Content-Type':'application/json'})
    response.end(JSON.stringify({id:'owned-visitor',platform_open_id:'owned-open',channel_id:'owned-channel',channel_type:251,
      im_token:'private-cli-im',visitor_access_token:missing?undefined:'private-cli-access',platform_id:'owned-platform',project_id:'owned-project'}))
  })
  await new Promise(done=>server.listen(0,'127.0.0.1',done))
  const run=command=>new Promise((done,reject)=>{
    const child=spawn(process.execPath,[join(repo,'dist/index.js'),'--server',`http://127.0.0.1:${server.address().port}`,
      '--api-key','fixture-public-key',command],{cwd:repo,windowsHide:true,env:{...process.env,TGO_WIDGET_CONFIG_PATH:config}})
    let output=''
    const timer=setTimeout(()=>{child.kill();reject(new Error('Owned init CLI did not finish'))},20000)
    child.stdout.on('data',value=>{output+=value})
    child.stderr.on('data',value=>{output+=value})
    child.on('error',error=>{clearTimeout(timer);reject(error)})
    child.on('close',code=>{clearTimeout(timer);done({output,code})})
  })
  try {
    const initialized=await run('init')
    assert.equal(initialized.code,0)
    assert.equal(initialized.output.includes('private-cli'),false)
    const stored=readFileSync(config,'utf8')
    assert.equal(JSON.parse(stored).visitor_access_token,'private-cli-access')
    assert.equal(JSON.parse(stored).project_id,'owned-project')
    const who=await run('whoami')
    assert.equal(who.output.includes('private-cli'),false)
    missing=true
    const failed=await run('init')
    assert.equal(failed.output.includes('缺少访问凭据'),true)
    assert.equal(failed.output.includes('private-cli'),false)
    assert.equal(readFileSync(config,'utf8'),stored)
  } finally {
    await new Promise(done=>server.close(done))
    const within=relative(parent,resolve(temporary))
    assert.ok(within&&!within.startsWith('..')&&!isAbsolute(within))
    rmSync(temporary,{recursive:true,force:true})
  }
})
