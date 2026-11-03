import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import {loadWebDataAccess} from './visitor-access-fixture.mjs'

const token='owned-profile-header-grant'
function fixture(file,fault) {
  let cached=fault==='missing' ? null : {visitor_id:'visitor',channel_id:'channel',im_token:token}
  const exports={},calls=[],access=loadWebDataAccess(() => cached)
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/'+file,import.meta.url),'utf8'),{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText,{exports,AbortController,setTimeout,clearTimeout,
    require:name => name==='./visitorDataAccess' ? access : {requireApiKeyOrThrow:() => 'public-key'},
    fetch:async (...args) => {
      calls.push(args)
      if (fault==='fetch') cached={...cached,im_token:'new-grant'}
      return {ok:true,status:200,json:async () => {
        if (fault==='body') cached={...cached,visitor_id:'other'}
        const result=file==='channel.ts' ? {name:'客服',avatar:'',channel_id:'staff-staff',channel_type:1,
          extra:{instruction:'must-not-return'}} : {id:'activity',activity_type:'page_view',title:'当前页'}
        if (fault==='echo') result.error=token
        return result
      }}
    },
  })
  return {exports,calls}
}
const variants=[['channel.ts','fetchChannelInfo',{apiBase:'http://owned.test',platformApiKey:'public-key',channelId:'staff-staff',channelType:1}],
  ['visitorActivity.ts','recordVisitorActivity',{apiBase:'http://owned.test',visitorId:'visitor',activityType:'page_view',title:'当前页'}]]
for (const [file,method,params] of variants) {
  test(file+' carries the original grant only in its header',async () => {
    const f=fixture(file)
    const result=await f.exports[method](params)
    assert.equal(f.calls[0][1].headers.Authorization,'Bearer '+token)
    assert.equal(JSON.stringify([f.calls[0][0],f.calls[0][1].body]).includes(token),false)
    assert.equal(JSON.stringify(result).includes('must-not-return'),false)
    if(file==='channel.ts') assert.equal(f.calls[0][0].includes('platform_api_key'),false)
  })
  for (const fault of ['missing','fetch','body','echo']) test(file+' refuses '+fault+' authority',async () => {
    const f=fixture(file,fault)
    await assert.rejects(f.exports[method](params))
    assert.equal(f.calls.length,fault==='missing' ? 0 : 1)
  })
}
test('activity visitor ID must match the original cache before HTTP',async () => {
  const f=fixture('visitorActivity.ts')
  await assert.rejects(f.exports.recordVisitorActivity({...variants[1][2],visitorId:'other'}))
  assert.equal(f.calls.length,0)
})
