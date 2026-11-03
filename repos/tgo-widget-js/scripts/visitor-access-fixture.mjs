import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

export function loadWebDataAccess(readCache = () => ({ visitor_id:'visitor',channel_id:'channel',im_token:'owned-header-grant' })) {
  const exports = {}
  const source = readFileSync(new URL('../src/services/visitorDataAccess.ts', import.meta.url),'utf8')
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText, {exports,require:() => ({loadCachedVisitor:readCache})})
  return exports
}
