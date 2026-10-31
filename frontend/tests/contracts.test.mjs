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

test('numeric configuration inputs preserve empty values and validate ranges', async () => {
  const app = await read('../src/app/App.tsx')
  assert.match(app, /raw === '' \? '' : Number\(raw\)/)
  assert.match(app, /function invalidNumericFields/)
  assert.match(app, /invalidNumbers\.length > 0/)
})

test('settings cannot be edited while a configuration save is in flight', async () => {
  const app = await read('../src/app/App.tsx')
  assert.ok(app.includes('disabled={isBusy || saving}'))
})

test('mailbox drafts survive navigation until the user explicitly reloads', async () => {
  const app = await read('../src/app/App.tsx')
  assert.ok(app.includes("if (page !== 'mailbox' || mailbox || mailboxDirty) return"))
  assert.ok(app.includes("onReload={() => void mailboxAction('reload')}"))
})

test('mailbox inputs and actions stay disabled until pool data is loaded', async () => {
  const app = await read('../src/app/App.tsx')
  assert.ok(app.includes('disabled={busy || !pool}'))
  assert.ok(app.includes('disabled={busy || !pool || !pool.data.trim()}'))
})

test('proxy preflight displays each target result and reports failures', async () => {
  const app = await read('../src/app/App.tsx')
  assert.ok(app.includes('preflight.targets.map'))
  assert.ok(app.includes("result.result.ok ? 'preflightPassed' : 'preflightFailed'"))
})

test('layout includes responsive mobile breakpoints', async () => {
  const css = await read('../src/styles.css')
  assert.match(css, /max-width: 980px/)
  assert.match(css, /max-width: 700px/)
  assert.match(css, /max-width: 420px/)
})
