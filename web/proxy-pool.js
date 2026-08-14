(() => {
  'use strict';

  const proxyFields = [
    ['proxy_mode','select',['auto','direct','single','pool']],
    ['proxy','text','full'],
    ['proxy_fallback','select',['none','direct','single']],
    ['proxy_pool_endpoint_mode','select',['auto','fixed','rotating']],
    ['proxy_pool_file','text','full'],
    ['proxy_pool_subscription_url','text','full'],
    ['proxy_pool_subscription_proxy','text','full'],
    ['proxy_pool_refresh_interval_sec','number',{min:0,max:86400}],
    ['proxy_pool_probe_interval_sec','number',{min:0,max:86400}],
    ['proxy_pool_probe_timeout_sec','number',{min:3,max:120}],
    ['proxy_pool_probe_provider','select',['cloudflare','ipinfo']],
    ['proxy_pool_max_concurrent_per_node','number',{min:1,max:64}],
    ['proxy_pool_acquire_timeout_sec','number',{min:1,max:600}],
    ['proxy_protocol_backend','select',['auto','sing-box','native-only']],
    ['proxy_singbox_path','text','full'],
    ['proxy_protocol_start_timeout_sec','number',{min:3,max:60}],
  ];

  const zh = {
    tabProxy:'代理池', proxyReload:'重新加载', proxyTest:'测试节点', proxyStatus:'代理节点状态',
    proxyEmpty:'暂无代理节点', proxyNode:'节点', proxyHealth:'健康度', proxyLatency:'延迟',
    proxyExitIP:'出口 IP', proxyInflight:'占用', proxyFailures:'失败', proxyCooldown:'冷却', proxyType:'类型',
    proxyProtocol:'协议', proxyBackend:'后端', proxySourceSummary:'订阅解析',
  };
  const en = {
    tabProxy:'Proxy pool', proxyReload:'Reload', proxyTest:'Test nodes', proxyStatus:'Proxy node status',
    proxyEmpty:'No proxy nodes', proxyNode:'Node', proxyHealth:'Health', proxyLatency:'Latency',
    proxyExitIP:'Exit IP', proxyInflight:'Inflight', proxyFailures:'Failures', proxyCooldown:'Cooldown', proxyType:'Type',
    proxyProtocol:'Protocol', proxyBackend:'Backend', proxySourceSummary:'Subscription parse',
  };
  Object.assign(i18n.zh, zh); Object.assign(i18n.en, en);
  Object.assign(i18n.zh.fields, {
    proxy_mode:['代理模式','auto 保持旧配置兼容；single/pool 启用账号级代理租约。'],
    proxy:['固定代理 / 单代理','auto 兼容旧代理；single 模式或 single fallback 使用。'],
    proxy_fallback:['代理池回退','none / direct / single。只在新账号租约获取前回退。'],
    proxy_pool_endpoint_mode:['节点类型','auto 会将含 {account} 的原生代理地址视为旋转代理入口。'],
    proxy_pool_file:['代理池文件','支持 HTTP/HTTPS/SOCKS/VLESS/VMess/Trojan/Hysteria2/TUIC；也支持 Base64 订阅文本。'],
    proxy_pool_subscription_url:['代理订阅 URL','支持普通文本或整份 Base64 编码的多协议节点订阅。'],
    proxy_pool_subscription_proxy:['订阅拉取代理','仅用于下载代理订阅，只接受 HTTP/HTTPS/SOCKS，可留空。'],
    proxy_pool_refresh_interval_sec:['订阅刷新间隔（秒）','0 表示关闭自动刷新。'],
    proxy_pool_probe_interval_sec:['健康探测间隔（秒）','0 表示关闭定期探测。'],
    proxy_pool_probe_timeout_sec:['探测超时（秒）','单节点健康检查超时。'],
    proxy_pool_probe_provider:['探测服务','用于验证代理连通性和出口 IP。'],
    proxy_pool_max_concurrent_per_node:['单节点最大并发','默认 1，避免多个注册 Session 共用同一固定出口。'],
    proxy_pool_acquire_timeout_sec:['租约等待超时（秒）','代理被占用或冷却时等待可用节点的最长时间。'],
    proxy_protocol_backend:['高级协议后端','auto：原生 HTTP/SOCKS 直接使用，高级协议自动通过 sing-box；native-only 禁用高级协议。'],
    proxy_singbox_path:['sing-box 路径','留空时从 PATH 自动寻找 sing-box；仅 VLESS/VMess/Trojan/Hysteria2/TUIC 需要。'],
    proxy_protocol_start_timeout_sec:['高级协议启动超时（秒）','等待本地 sing-box HTTP 出口就绪的最长时间。'],
  });
  Object.assign(i18n.en.fields, {
    proxy_mode:['Proxy mode','auto preserves legacy behavior; single/pool enables account-scoped leases.'],
    proxy:['Fixed / single proxy','Used by legacy auto mode, single mode, or single fallback.'],
    proxy_fallback:['Pool fallback','none / direct / single; applied only before a new account lease starts.'],
    proxy_pool_endpoint_mode:['Endpoint type','auto treats native URLs containing {account} as rotating gateways.'],
    proxy_pool_file:['Proxy pool file','Supports HTTP/HTTPS/SOCKS/VLESS/VMess/Trojan/Hysteria2/TUIC and Base64 subscription text.'],
    proxy_pool_subscription_url:['Subscription URL','Supports plain-text or whole-file Base64 multi-protocol subscriptions.'],
    proxy_pool_subscription_proxy:['Subscription fetch proxy','Used only to download the subscription; HTTP/HTTPS/SOCKS only.'],
    proxy_pool_refresh_interval_sec:['Refresh interval (seconds)','0 disables automatic source refresh.'],
    proxy_pool_probe_interval_sec:['Probe interval (seconds)','0 disables periodic probes.'],
    proxy_pool_probe_timeout_sec:['Probe timeout (seconds)','Timeout for one node health check.'],
    proxy_pool_probe_provider:['Probe provider','Used to verify connectivity and exit IP.'],
    proxy_pool_max_concurrent_per_node:['Max sessions per node','Defaults to 1 to avoid sharing one fixed exit across account sessions.'],
    proxy_pool_acquire_timeout_sec:['Lease wait timeout (seconds)','Maximum wait while nodes are busy or cooling down.'],
    proxy_protocol_backend:['Advanced protocol backend','auto keeps HTTP/SOCKS native and routes advanced protocols through sing-box; native-only disables them.'],
    proxy_singbox_path:['sing-box path','Leave blank to resolve sing-box from PATH; only required for VLESS/VMess/Trojan/Hysteria2/TUIC.'],
    proxy_protocol_start_timeout_sec:['Advanced protocol startup timeout','Maximum wait for the local sing-box HTTP endpoint to become ready.'],
  });

  icons.proxy = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="12" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="18" cy="18" r="2"/><path d="M8 11l8-4M8 13l8 4"/></svg>';
  fieldDefs.basic = fieldDefs.basic.filter(([key]) => key !== 'proxy');
  fieldDefs.proxy = proxyFields;
  tabMeta.proxy = ['tabProxy','proxy'];

  renderFields();
  applyLanguage();

  const proxyTab = document.querySelector('[data-tabkey="tabProxy"]');
  const basicTab = document.querySelector('[data-tabkey="tabBasic"]');
  if (proxyTab && basicTab && basicTab.nextSibling !== proxyTab) basicTab.after(proxyTab);
  const proxySection = document.getElementById('sec-proxy');
  const basicSection = document.getElementById('sec-basic');
  if (proxySection && basicSection && basicSection.nextSibling !== proxySection) basicSection.after(proxySection);

  if (proxySection) {
    const shell = document.createElement('div');
    shell.className = 'proxy-status-shell';
    shell.innerHTML = `
      <div class="proxy-status-head">
        <strong data-i18n="proxyStatus">${t('proxyStatus')}</strong>
        <div class="proxy-status-actions">
          <button type="button" id="proxyReloadBtn" class="mini-btn"><span data-i18n="proxyReload">${t('proxyReload')}</span></button>
          <button type="button" id="proxyTestBtn" class="mini-btn"><span data-i18n="proxyTest">${t('proxyTest')}</span></button>
        </div>
      </div>
      <div id="proxyPoolSummary" class="proxy-summary"></div>
      <div id="proxySourceSummary" class="proxy-summary"></div>
      <div class="proxy-table-wrap"><table class="proxy-table"><thead><tr>
        <th data-i18n="proxyNode">${t('proxyNode')}</th><th data-i18n="proxyProtocol">${t('proxyProtocol')}</th>
        <th data-i18n="proxyBackend">${t('proxyBackend')}</th><th data-i18n="proxyType">${t('proxyType')}</th>
        <th data-i18n="proxyHealth">${t('proxyHealth')}</th><th data-i18n="proxyLatency">${t('proxyLatency')}</th>
        <th data-i18n="proxyExitIP">${t('proxyExitIP')}</th><th data-i18n="proxyInflight">${t('proxyInflight')}</th>
        <th data-i18n="proxyFailures">${t('proxyFailures')}</th><th data-i18n="proxyCooldown">${t('proxyCooldown')}</th>
      </tr></thead><tbody id="proxyPoolRows"></tbody></table></div>`;
    proxySection.appendChild(shell);
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function renderSourceSummary(data) {
    const target = document.getElementById('proxySourceSummary');
    if (!target) return;
    const sources = data && data.sources && typeof data.sources === 'object' ? data.sources : {};
    const parts = [];
    for (const key of ['subscription','file']) {
      const source = sources[key];
      if (!source || typeof source !== 'object') continue;
      const counts = source.protocol_counts || {};
      const protocolText = Object.entries(counts).map(([name,count]) => `${name}:${count}`).join(' · ');
      parts.push(`${key}: ${source.supported || 0}/${source.total_lines || 0}${source.decoded_base64 ? ' · Base64' : ''}${protocolText ? ' · '+protocolText : ''}${source.skipped ? ' · skipped:'+source.skipped : ''}`);
    }
    if (Array.isArray(sources.errors) && sources.errors.length) parts.push(`errors: ${sources.errors.join(' | ')}`);
    target.textContent = parts.join(' || ');
  }
  function renderProxyStatus(data) {
    const rows = document.getElementById('proxyPoolRows');
    const summary = document.getElementById('proxyPoolSummary');
    if (!rows || !summary) return;
    const nodes = Array.isArray(data.nodes) ? data.nodes : [];
    summary.textContent = `${data.mode || 'auto'} · ${nodes.length} nodes`;
    renderSourceSummary(data);
    if (!nodes.length) {
      rows.innerHTML = `<tr><td colspan="10" class="proxy-empty">${esc(t('proxyEmpty'))}</td></tr>`;
      return;
    }
    rows.innerHTML = nodes.map(node => {
      const status = node.probe_status === 'healthy' ? 'good' : (node.probe_status === 'unhealthy' || node.probe_status === 'unavailable') ? 'bad' : '';
      const label = node.name ? `${node.name} · ${node.proxy}` : node.proxy;
      return `<tr>
        <td title="${esc(node.id)}"><span class="proxy-dot ${status}"></span>${esc(label)}</td>
        <td>${esc(node.protocol || '—')}</td><td>${esc(node.backend || 'native')}</td>
        <td>${node.rotating ? 'rotating' : 'fixed'}</td><td>${esc(node.health)}</td>
        <td>${node.probe_latency_ms ? esc(node.probe_latency_ms)+' ms' : '—'}</td><td>${esc(node.exit_ip || '—')}</td>
        <td>${esc(node.inflight)}</td><td>${esc(node.failure_count)}</td><td>${node.cooldown_sec ? esc(node.cooldown_sec)+' s' : '—'}</td>
      </tr>`;
    }).join('');
  }
  async function refreshProxyStatus() {
    try {
      const r = await fetch('/api/proxy-pool/status');
      if (!r.ok) return;
      renderProxyStatus(await r.json());
    } catch (_) {}
  }
  async function proxyAction(path) {
    if (dirty.size && !await saveConfig()) return;
    const reload = document.getElementById('proxyReloadBtn');
    const test = document.getElementById('proxyTestBtn');
    if (reload) reload.disabled = true; if (test) test.disabled = true;
    try {
      const r = await fetch(path,{method:'POST'}); const d = await r.json();
      if (!r.ok) setNotice(d.detail || 'Proxy pool operation failed', true);
      else { renderProxyStatus(d); setNotice(''); }
    } catch (e) { setNotice(e.message, true); }
    finally { if (reload) reload.disabled = !!running; if (test) test.disabled = !!running; }
  }
  const reloadBtn = document.getElementById('proxyReloadBtn');
  const testBtn = document.getElementById('proxyTestBtn');
  if (reloadBtn) reloadBtn.onclick = () => proxyAction('/api/proxy-pool/reload');
  if (testBtn) testBtn.onclick = () => proxyAction('/api/proxy-pool/test');

  loadConfig().catch(e => setNotice(e.message,true));
  refreshProxyStatus();
  setInterval(() => {
    if (reloadBtn) reloadBtn.disabled = !!running;
    if (testBtn) testBtn.disabled = !!running;
    refreshProxyStatus();
  }, 2000);
})();
