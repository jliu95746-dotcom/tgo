import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

export function generationFixture() {
  const exports={}
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/visitorGeneration.ts',import.meta.url),'utf8'),{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText,{exports})
  return exports
}
