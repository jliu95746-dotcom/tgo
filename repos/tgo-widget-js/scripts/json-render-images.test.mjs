import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import test from 'node:test'

const privateUrl = 'https://untrusted.test/v1/chat/files/11111111-1111-4111-8111-111111111111?file_token=old'

for (const [name, registryPath, hookImport] of [
  ['visitor widget', '../src/components/jsonRender/registry.tsx', '../../store/chatFileAccess'],
  ['staff console', '../../tgo-web/src/components/chat/jsonRender/registry.tsx', '@/hooks/useChatFileUrls'],
]) {
  function renderImage(source, authorizedUrl) {
    const requests = []
    const exports = {}
    const sourceCode = readFileSync(new URL(registryPath, import.meta.url), 'utf8')
    const compiled = ts.transpileModule(sourceCode, { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText
    vm.runInNewContext(compiled, {
      exports,
      require: key => {
        if (key === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
        if (key === '@json-render/react') return { useBoundProp: () => ['', () => {}] }
        if (key === hookImport) return { useChatFileUrls: urls => { requests.push(urls); return [authorizedUrl] } }
        throw new Error(`Unexpected import: ${key}`)
      },
    })
    return { element: exports.jsonRenderRegistry.Image({ element: { props: { src: source, alt: '商品图' } } }), requests }
  }

  test(`${name} JSON Render hides protected image until authorized`, () => {
    const pending = renderImage(privateUrl, '')
    assert.deepEqual(JSON.parse(JSON.stringify(pending.requests)), [[privateUrl]])
    assert.equal(pending.element, null)

    const authorized = renderImage(privateUrl, 'https://owned.test/v1/chat/files/11111111-1111-4111-8111-111111111111?file_token=short-lived')
    assert.equal(authorized.element.type, 'img')
    assert.equal(authorized.element.props.src.includes('old'), false)
    assert.equal(authorized.element.props.referrerPolicy, 'no-referrer')

    const publicImage = renderImage('https://example.test/product.jpg', 'https://example.test/product.jpg')
    assert.equal(publicImage.element.props.src, 'https://example.test/product.jpg')
  })
}
