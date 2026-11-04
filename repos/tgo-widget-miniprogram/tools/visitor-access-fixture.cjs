const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

function loadMiniDataAccess(readCache) {
  const module = {exports:{}}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/services/visitorDataAccess.js'),'utf8'),{
    module,require:() => ({loadCachedVisitor:readCache}),
  })
  return module.exports
}
module.exports = {loadMiniDataAccess}
