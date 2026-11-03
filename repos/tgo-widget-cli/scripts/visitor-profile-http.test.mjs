import assert from 'node:assert/strict'
import {createServer} from 'node:http'
import {spawn} from 'node:child_process'
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs'
import {resolve,join,dirname,relative,isAbsolute} from 'node:path'
import {fileURLToPath} from 'node:url'
import test from 'node:test'

for (const command of ['channel','activity']) test('built '+command+' uses original grant and rejects stale/echoed output',async () => {
  const repo=resolve(dirname(fileURLToPath(import.meta.url)),'..'),parent=resolve(repo,'../../.tmp')
  const directory=mkdtempSync(join(parent,'visitor-profile-cli-')),config=join(directory,'config.json')
  const token='owned-profile-cli-header-grant'
  let mode='normal',identity
  const calls=[],save=value => writeFileSync(config,JSON.stringify(value))
  const server=createServer(async (request,response) => {
    let body='';for await(const chunk of request) body+=chunk
    calls.push({url:request.url,headers:request.headers,body})
    if (mode==='change') save({...identity,im_token:'new-grant'})
    response.writeHead(200,{'Content-Type':'application/json'})
    response.end(JSON.stringify({name:mode==='echo' ? token : '当前访客',avatar:'',
      channel_id:'owned-channel',channel_type:251,entity_type:'visitor'}))
  })
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve))
  const url='http://127.0.0.1:'+server.address().port
  identity={server:url,api_key:'public-key',visitor_id:'owned-visitor',im_token:token,channel_id:'owned-channel',channel_type:251}
  const run=(extra=[]) => new Promise((resolve,reject) => {
    const arguments_ = command==='channel' ? ['channel','info'] : ['activity','record','--type','page_view','--title','当前页']
    const child=spawn(process.execPath,[join(repo,'dist/index.js'),'--server',url,'--api-key','public-key',...extra,...arguments_],{
      cwd:repo,env:{...process.env,TGO_WIDGET_CONFIG_PATH:config,TGO_WIDGET_SERVER:'',TGO_WIDGET_API_KEY:''},windowsHide:true,
    })
    let stdout='',stderr=''
    const timer=setTimeout(() => {child.kill();reject(new Error('owned CLI timeout'))},15000)
    child.stdout.on('data',value => {stdout+=value});child.stderr.on('data',value => {stderr+=value})
    child.on('error',error => {clearTimeout(timer);reject(error)})
    child.on('close',code => {clearTimeout(timer);resolve({code,stdout,stderr})})
  })
  try {
    save(identity)
    const good=await run()
    assert.equal(good.code,0)
    assert.ok(good.stdout.includes('当前访客'))
    assert.equal(calls[0].headers.authorization,'Bearer '+token)
    assert.equal(JSON.stringify([calls[0].url,calls[0].body]).includes(token),false)
    assert.equal(calls[0].url.includes('platform_api_key'),false)
    for (const state of ['change','echo']) {
      mode=state;save(identity)
      const denied=await run()
      assert.equal(denied.code,1)
      assert.equal((denied.stdout+denied.stderr).includes(token),false)
      assert.equal(denied.stdout.includes('当前访客'),false)
    }
    const count=calls.length
    save({...identity,im_token:undefined})
    assert.equal((await run()).code,1)
    assert.equal(calls.length,count)
    save(identity)
    assert.equal((await run(['--server','http://different.invalid'])).code,1)
    assert.equal(calls.length,count)
  } finally {
    await new Promise(resolve => server.close(resolve))
    const relation=relative(parent,resolve(directory))
    assert.ok(relation && !relation.startsWith('..') && !isAbsolute(relation))
    rmSync(directory,{recursive:true,force:true})
  }
})
