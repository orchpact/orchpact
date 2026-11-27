import {mountConsole,openEditor} from '/static/ui/admin.js';
import {readRoute, resolveStudio, fragmentOf} from './studio-route.js';
import {showWorkflows,showCanvasHome} from './workflows.js';
import {showRuns, showRun, showInbox} from './runs.js';
import {showProviders, showPolicy, showAudit, showInstance} from './settings.js';
import {OrchpactOrganizations} from './organizations.js';
import {showAccount} from '../account/app.js';

const $ = selector => document.querySelector(selector);
const content = $('#content');
const main = $('#console-main');
const settingsPages = ['providers', 'organizations', 'policies', 'audit', 'instance', 'account'];
const isAdminPage = location.pathname === '/admin/';
const label = {
  queued:'等待执行', running:'执行中', waiting:'等待依赖或决定', awaiting_approval:'外部写操作待审批',
  rejected:'已拒绝', completed:'运行完成', partial:'部分完成', failed:'失败', interrupted:'执行中断',
  cancelled:'已取消', accepted:'已验收', pending:'待验收', waived:'缺口已记录', unknown:'结果未知',
  review:'待验收', PASS:'检查通过', FAIL:'检查失败', INCONCLUSIVE:'证据不足',
  started:'已开始', settled:'已结算', used:'已使用', approved:'已批准', stale:'已失效',
};
const drawer = $('#resource-drawer'), drawerContent = $('#drawer-content');
let drawerController, mountedDocument, mountedDrawer, activeDocumentHash = 'canvas', activeSpace;
let me, pageController, activeLocation, dialogVersion = 0, initialized = false;

let workspaceId = '', activeDismiss;
const updateChrome = mountConsole({
  refresh() { tell(''); mountedDrawer = undefined; render(); },
  async logout() {
    try { await api('session', {method:'DELETE'}); signIn(); }
    catch (error) { tell(error.message, true); }
  },
  closeDrawer,
  changeSpace(id) {
    const url = setWorkspace(id);
    if (!settingsPages.includes(readRoute(location.hash).page)) url.hash = 'canvas';
    history.replaceState(null, '', url);
    tell('');
    render();
  },
});

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

function tell(value, error = false) {
  const notice = $('#notice');
  notice.textContent = value;
  notice.className = error ? 'error' : '';
}

function heading(title, description, action = '') {
  return '<div class="heading"><div><h1>'+esc(title)+'</h1><p class="muted">'+esc(description)+'</p></div>'+action+'</div>';
}

function empty(text) { return '<div class="panel muted">'+esc(text)+'</div>'; }
function signIn() {
  location.assign('/account/?'+new URLSearchParams({return:location.pathname+location.search+location.hash}));
}

async function api(path, {method = 'GET', value, signal, headers = {}, space = workspaceId} = {}) {
  const response = await fetch('/api/v1/'+path, {
    method, credentials:'same-origin', signal,
    headers:{'Content-Type':'application/json', ...(space ? {'X-Workspace-Id':space} : {}), ...headers},
    ...(value === undefined ? {} : {body:JSON.stringify(value)}),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && (!space || space === workspaceId)) signIn();
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : data.detail;
    throw Object.assign(Error(detail || '请求失败'), {status:response.status, code:data.code});
  }
  return data;
}

function formDialog({title, body, caption = '保存', mount, onSubmit}, signal) {
  activeDismiss?.();
  const space = workspaceId;
  const version = ++dialogVersion;
  let close;
  const unmount = openEditor({title, caption, onDismiss:() => {
    if (!close?.disabled) dismiss();
  }});
  const form = $('#edit-form'), fields = $('#edit-fields'), submit = $('#save-editor');
  const errorText = $('#edit-error');
  close = $('#close-editor');
  fields.innerHTML = body;
  function dismiss() {
    if (activeDismiss !== dismiss) return;
    activeDismiss = undefined;
    ++dialogVersion;
    signal?.removeEventListener('abort',dismiss);
    unmount();
  }
  activeDismiss = dismiss;
  let last = '', key;
  const keyFor = value => {
    const signature = JSON.stringify(value);
    if (signature !== last || !key) { last = signature; key = crypto.randomUUID(); }
    return key;
  };
  function busy(value) {
    close.disabled = value;
    submit.disabled = value;
    form.toggleAttribute('aria-busy', value);
  }
  form.onsubmit = async event => {
    event.preventDefault();
    if (close.disabled) return;
    busy(true);
    errorText.textContent = '';
    try {
      if (space !== workspaceId) throw Error('当前空间已切换，请回到原空间继续');
      await onSubmit(form, {keyFor, submit, close:dismiss});
    } catch (error) {
      if (version !== dialogVersion) return;
      errorText.textContent = error.message;
      globalThis.OrchpactTemplateForm.showError(fields,error.message);
    } finally {
      if (version === dialogVersion) busy(false);
    }
  };
  mount?.(form, submit);
  if (signal?.aborted) { dismiss(); return; }
  signal?.addEventListener('abort',dismiss,{once:true});
  const initial = fields.querySelector('input:not([type=hidden]):not(:disabled),select:not(:disabled),textarea:not(:disabled),button:not(:disabled)');
  (initial || close).focus({preventScroll:true});
}

function jsonDialog(title,value,help,action,open,refresh) {
  open({
    title,
    body:'<p class="muted">'+esc(help)+'</p><label>JSON 配置<textarea name="json" rows="16" spellcheck="false" required>'
      +esc(JSON.stringify(value,null,2))+'</textarea></label>',
    onSubmit:async (node, modal) => {
      const payload = JSON.parse(node.elements.json.value);
      const next = await action(payload, modal.keyFor(payload));
      modal.close();
      tell('已保存');
      if (typeof next === 'function') next();
      else await refresh();
    },
  });
}

function setNavigation(page, panel) {
  const isSettings = settingsPages.includes(page);
  updateChrome({current:panel || (isSettings ? page : 'canvas'), drawer:panel || null});
}

function updateIdentity() {
  const item = me.workspaces.find(w => w.id === workspaceId);
  const owner = me.admin || item?.role === 'owner';
  updateChrome({session:me, owner, workspace:workspaceId});
  return owner;
}

function setWorkspace(next) {
  const valid = id => me.workspaces.some(item => item.id === id);
  const saved = sessionStorage.getItem('orchpact.workspace:'+me.user_id);
  const selected = [next,saved,me.workspace_id].find(valid) || me.workspaces[0]?.id || '';
  workspaceId = selected;
  const url = new URL(location.href);
  if (selected) {
    url.searchParams.set('workspace',selected);
    sessionStorage.setItem('orchpact.workspace:'+me.user_id,selected);
  } else url.searchParams.delete('workspace');
  return url;
}

async function refreshIdentity(signal) {
  const session = await api('auth/me',{space:'',signal});
  if (signal?.aborted) throw new DOMException('Page changed','AbortError');
  me = session;
  if (isAdminPage && !me.admin) {
    location.replace('/workspace/'+location.search+location.hash);
    throw new DOMException('Admin session ended','AbortError');
  }
  const url = setWorkspace(workspaceId || new URLSearchParams(location.search).get('workspace'));
  if (url.href !== location.href) history.replaceState(null,'',url);
  updateIdentity();
  return me;
}

function openWorkspace(id) {
  if (!me.workspaces.some(item => item.id === id)) return;
  const url = setWorkspace(id);
  url.hash = 'canvas';
  tell('');
  history.pushState(null,'',url);
  main.focus();
  window.scrollTo(0,0);
  render();
}

function scopedUI(route, host, controller, selected, owner) {
  const signal = controller.signal;
  const scopedApi = (path, options = {}) => api(path, {
    space:selected, ...(!options.method ? {signal} : {}), ...options,
  });
  const open = options => formDialog(options,signal);
  const refresh = host === drawerContent ? () => {mountedDrawer = undefined;return render(false);} : render;
  return {content:host, api:scopedApi, esc, heading, empty, tell, label, render:refresh, formDialog:open,
    jsonDialog:(title,value,help,action)=>jsonDialog(title,value,help,action,open,refresh),
    current:() => !signal.aborted && (route.page === 'account' || workspaceId === selected),
    params:route.params, key:route.key, owner, me, workspace:() => workspaceId, signal};
}

async function renderDocument(route, ui) {
  const {page, key} = route;
  if (page === 'canvas') return showCanvasHome(ui);
  if (page === 'account') return showAccount({...ui,
    globalApi:(path, options) => api(path,{...options,space:''}), refreshIdentity,openWorkspace});
  if (page === 'workflows') return showWorkflows(ui);
  if (page === 'projects' && key) return showRun(ui);
  if (page === 'organizations' && ui.owner) {
    const state = await ui.api('workbench/organizations');
    if (ui.current()) OrchpactOrganizations.show(ui.content,state,ui);
    return;
  }
  if (page === 'providers' && me.admin) return showProviders(ui);
  if (page === 'policies' && me.admin) return showPolicy(ui);
  if (page === 'audit' && me.admin) return showAudit(ui);
  if (page === 'instance' && me.admin) return showInstance(ui);
  ui.content.innerHTML = empty('当前身份没有此页面的访问权限。');
}

async function display(ui, action) {
  try { await action(); }
  catch (error) {
    if (error.name === 'AbortError' || !ui.current()) return;
    ui.content.innerHTML = empty(error.message);
    tell(error.message,true);
  }
}

async function render(force = true) {
  if (!initialized) return;
  const selected = workspaceId;
  const state = resolveStudio(location.hash, selected === activeSpace ? activeDocumentHash : 'canvas');
  if (location.hash && location.hash.slice(1) !== state.hash) history.replaceState(null,'','#'+state.hash);
  activeLocation = location.href;
  activeDocumentHash = state.documentHash;
  activeSpace = selected;
  const owner = updateIdentity(), panel = state.drawer;
  setNavigation(state.doc.page, panel?.page);
  document.body.classList.toggle('in-studio',state.studio);
  const pending = [];
  const docKey = JSON.stringify([selected, state.documentHash]);
  if (force || mountedDocument !== docKey) {
    mountedDocument = docKey;
    pageController?.abort();
    pageController = new AbortController();
    const ui = scopedUI(state.doc, content, pageController, selected, owner);
    content.innerHTML = '<p class="muted">正在读取…</p>';
    pending.push(display(ui, () => renderDocument(state.doc, ui)));
  }

  const drawerKey = panel ? JSON.stringify([selected,fragmentOf(panel)]) : '';
  if (mountedDrawer !== drawerKey) {
    drawerController?.abort();
    mountedDrawer = drawerKey;
    drawerContent.replaceChildren();
    const wasOpen = !drawer.hidden;
    drawer.hidden = !panel;
    if (panel) {
      drawerController = new AbortController();
      const ui = scopedUI(panel, drawerContent, drawerController, selected, owner);
      const captions = {workflows:'工作流库', projects:'运行记录', attention:'待处理'};
      const caption = captions[panel.page];
      $('#drawer-caption').textContent = caption;
      ui.heading = (title,description,action='') => title === caption ? action : heading(title,description,action);
      drawerContent.innerHTML = '<p class="muted">正在读取…</p>';
      if (!wasOpen) $('#close-drawer').focus({preventScroll:true});
      const browse = {workflows:showWorkflows, projects:showRuns, attention:showInbox};
      pending.push(display(ui, () => browse[panel.page](ui)));
    } else if (wasOpen && document.activeElement === document.body) main.focus({preventScroll:true});
  }
  await Promise.all(pending);
}

function closeDrawer() {
  if (drawer.hidden) return;
  const trigger = document.querySelector('.console-nav [aria-expanded="true"]');
  location.hash = activeDocumentHash;
  trigger?.focus({preventScroll:true});
}
$('#close-drawer').onclick = closeDrawer;
$('.skip-link').onclick = event => {event.preventDefault();main.focus();};
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !drawer.hidden && !document.querySelector('dialog[open],[role="dialog"]')) {
    closeDrawer();
  }
});
function followLocation() {
  if (location.href === activeLocation) return;
  const requested = new URLSearchParams(location.search).get('workspace');
  if (me && requested !== workspaceId) history.replaceState(null,'',setWorkspace(requested));
  tell('');
  render(false);
}
window.addEventListener('hashchange',followLocation);
window.addEventListener('popstate',followLocation);

try {
  await refreshIdentity();
  initialized = true;
  await render();
} catch(error) {
  if (error.name !== 'AbortError') tell(error.message,true);
}
