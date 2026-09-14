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
    '<span id="outlookPoolStatus" class="field-help" style="margin-left:auto"></span>',
    '</div>'
  ].join('');
  const grid = pathInput.closest('.grid') || pathInput.parentElement.parentElement;
  grid.appendChild(wrapper);

  const editor = document.getElementById('outlookMailboxPoolData');
  const status = document.getElementById('outlookPoolStatus');
  const loadBtn = document.getElementById('outlookPoolLoad');
  const saveBtn = document.getElementById('outlookPoolSave');

  function setStatus(text, error) {
    status.textContent = text || '';
    status.style.color = error ? '#ff7f7f' : '#707079';
  }

  function syncVisibility() {
    wrapper.hidden = provider.value !== 'outlook';
  }

  async function loadPool() {
    setStatus('Loading…', false);
    const response = await fetch('/api/mailboxes/outlook', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to load Outlook mailbox pool');
    editor.value = data.data || '';
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
  syncVisibility();
  if (provider.value === 'outlook') {
    loadPool().catch(function (error) { setStatus(error.message, true); });
  }
})();
