import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

test('React entrypoint loads the app and shared stylesheet', async () => {
  const entry = await read('../src/main.tsx')
  assert.match(entry, /createRoot/)
  assert.match(entry, /\.\/app\/App/)
  assert.match(entry, /\.\/styles\.css/)
})

test('the workspace keeps all primary operator workflows', async () => {
  const app = await read('../src/app/App.tsx')
  for (const page of ['overview', 'settings', 'proxy', 'mailbox', 'console']) {
    assert.ok(app.includes("'" + page + "'"), 'missing page: ' + page)
  }
  for (const endpoint of [
    '/api/config', '/api/status', '/api/logs', '/api/start', '/api/stop',
    '/api/health-check', '/api/proxy-pool/status', '/api/proxy-pool/reload',
    '/api/proxy-pool/test', '/api/proxy-pool/preflight',
    '/api/mailboxes/outlook', '/api/mailboxes/outlook/test',
  ]) {
    assert.ok(app.includes(endpoint), 'missing API integration: ' + endpoint)
  }
})

test('layout includes responsive mobile breakpoints', async () => {
  const css = await read('../src/styles.css')
  assert.match(css, /max-width: 980px/)
  assert.match(css, /max-width: 700px/)
  assert.match(css, /max-width: 420px/)
})
