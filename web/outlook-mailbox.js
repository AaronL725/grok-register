(function () {
  'use strict';

  const provider = document.getElementById('email_provider');
  const pathInput = document.getElementById('outlook_accounts_file');
  if (!provider || !pathInput) return;

  const wrapper = document.createElement('div');
  wrapper.className = 'field full';
  wrapper.id = 'outlookMailboxPoolEditor';
  wrapper.innerHTML = [
    '<label class="field-label" for="outlookMailboxPoolData">Outlook mailbox pool</label>',
    '<div class="field-help">email----password----clientId----refreshToken----auto/imap/graph</div>',
    '<textarea id="outlookMailboxPoolData" spellcheck="false" autocomplete="off" ',
    'style="width:100%;min-height:180px;resize:vertical;border:1px solid #2d2d32;border-radius:8px;',
    'background:#0b0b0d;color:#f5f5f5;padding:10px;font:12px/1.45 SFMono-Regular,Consolas,monospace;',
    'outline:none"></textarea>',
    '<div style="display:flex;gap:8px;align-items:center;margin-top:8px">',
    '<button id="outlookPoolLoad" type="button" class="mini-btn">Load pool</button>',
    '<button id="outlookPoolSave" type="button" class="mini-btn">Save pool</button>',
    '<button id="outlookPoolTest" type="button" class="mini-btn">Test pool</button>',
    '<span id="outlookPoolStatus" class="field-help" style="margin-left:auto"></span>',
    '</div>',
    '<pre id="outlookPoolHealth" hidden ',
    'style="margin-top:8px;max-height:220px;overflow:auto;white-space:pre-wrap;border:1px solid #242429;',
    'border-radius:8px;padding:8px;background:#0b0b0d;color:#b9b9c2;font:12px/1.45 SFMono-Regular,Consolas,monospace"></pre>'
  ].join('');
  const grid = pathInput.closest('.grid') || pathInput.parentElement.parentElement;
  grid.appendChild(wrapper);

  const editor = document.getElementById('outlookMailboxPoolData');
  const status = document.getElementById('outlookPoolStatus');
  const health = document.getElementById('outlookPoolHealth');
  const loadBtn = document.getElementById('outlookPoolLoad');
  const saveBtn = document.getElementById('outlookPoolSave');
  const testBtn = document.getElementById('outlookPoolTest');

  function setStatus(text, error) {
    status.textContent = text || '';
    status.style.color = error ? '#ff7f7f' : '#707079';
  }

  function syncVisibility() {
    wrapper.hidden = provider.value !== 'outlook';
  }

  function channelLabel(item, key) {
    const state = item && item[key] ? item[key] : {};
    if (state.ok) {
      const folders = Array.isArray(state.folders) && state.folders.length ? ' [' + state.folders.join(', ') + ']' : '';
      return key.toUpperCase() + ': OK' + folders;
    }
    return key.toUpperCase() + ': FAIL' + (state.error ? ' (' + state.error + ')' : '');
  }

  function renderHealth(data) {
    const results = Array.isArray(data.results) ? data.results : [];
    const lines = [
      'Healthy: ' + data.healthy + '/' + data.count +
        ' · IMAP: ' + data.imap + ' · Graph: ' + data.graph
    ];
    results.forEach(function (item) {
      lines.push(
        (item.usable ? '✓ ' : '✗ ') + item.email + ' [' + item.mode + '] · ' +
        channelLabel(item, 'imap') + ' · ' + channelLabel(item, 'graph')
      );
    });
    health.textContent = lines.join('\n');
    health.hidden = false;
  }

  async function loadPool() {
    setStatus('Loading…', false);
    const response = await fetch('/api/mailboxes/outlook', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to load Outlook mailbox pool');
    editor.value = data.data || '';
    health.hidden = true;
    setStatus('Valid: ' + data.count + ' · Invalid: ' + data.invalid + ' · Duplicates: ' + (data.duplicates || []).length, false);
  }

  async function savePool() {
    setStatus('Saving…', false);
    const configResponse = await fetch('/api/config', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({outlook_accounts_file: pathInput.value || './output/mailboxes/outlook-accounts.txt'})
    });
    const configData = await configResponse.json();
    if (!configResponse.ok) throw new Error(configData.detail || 'Failed to save Outlook pool path');
    const response = await fetch('/api/mailboxes/outlook', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({data: editor.value})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to save Outlook mailbox pool');
    setStatus('Saved · Valid: ' + data.count, false);
  }

  async function testPool() {
    setStatus('Testing mailbox access…', false);
    testBtn.disabled = true;
    try {
      const response = await fetch('/api/mailboxes/outlook/test', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        cache: 'no-store',
        body: JSON.stringify({data: editor.value})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Failed to test Outlook mailbox pool');
      renderHealth(data);
      setStatus('Health check complete · Healthy: ' + data.healthy + '/' + data.count, data.unhealthy > 0);
    } finally {
      testBtn.disabled = false;
    }
  }

  provider.addEventListener('change', function () {
    syncVisibility();
    if (provider.value === 'outlook' && !editor.value) {
      loadPool().catch(function (error) { setStatus(error.message, true); });
    }
  });
  loadBtn.addEventListener('click', function () {
    loadPool().catch(function (error) { setStatus(error.message, true); });
  });
  saveBtn.addEventListener('click', function () {
    savePool().catch(function (error) { setStatus(error.message, true); });
  });
  testBtn.addEventListener('click', function () {
    testPool().catch(function (error) { setStatus(error.message, true); });
  });
  syncVisibility();
  if (provider.value === 'outlook') {
    loadPool().catch(function (error) { setStatus(error.message, true); });
  }
})();
