import React, { useState, useSyncExternalStore } from 'react';
import { createPortal, flushSync } from 'react-dom';
import { createRoot } from 'react-dom/client';
import { Code2, History, Inbox, Layers, LayoutGrid, LogOut, PanelLeft, RefreshCw, SlidersHorizontal, UserRound } from 'lucide-react';
import {
  Badge, Button, Dialog, DialogContent, DialogTitle, Separator, Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger,
} from './components/ui';
import './graph.jsx';

const glyphs = {
  canvas:LayoutGrid, workflows:Layers, projects:History, attention:Inbox, settings:SlidersHorizontal,
  refresh:RefreshCw, account:UserRound, exit:LogOut, api:Code2, space:PanelLeft,
};

function Icon({ name }) {
  const Glyph = glyphs[name];
  return <Glyph aria-hidden="true" className="console-icon"/>;
}

const destinations = [
  ['canvas', '画布'], ['workflows', '工作流库'], ['projects', '运行记录'], ['attention', '待处理'],
];
const settings = [
  ['providers', '模型与工具', 'admin'], ['organizations', '组织与角色', 'owner'],
  ['policies', '治理规则', 'admin'], ['audit', '操作审计', 'admin'], ['instance', '实例状态', 'admin'],
  ['account', '成员与账号'],
];

function AppearanceSelect() {
  const appearance = window.OrchpactAppearance;
  const current = useSyncExternalStore(appearance.subscribe, appearance.current);
  return <label className="appearance-setting">
    <span>界面外观</span>
    <select value={current} onChange={event => appearance.select(event.target.value)}>
      <option value="system">跟随系统</option>
      <option value="light">浅色</option>
      <option value="dark">深色</option>
    </select>
  </label>;
}

function PlatformSettings({ session, owner, current }) {
  const [open, setOpen] = useState(false);
  const allowed = settings.filter(([, , permission]) => !permission || (permission === 'admin' ? session?.admin : owner));
  return <Sheet open={open} onOpenChange={setOpen}>
    <SheetTrigger asChild>
      <Button id="advanced-navigation" variant="ghost" className="settings-trigger"
        data-current={settings.some(([key]) => current === key) ? '' : undefined}>
        <Icon name="settings"/><span>平台设置</span>
      </Button>
    </SheetTrigger>
    <SheetContent side="left" className="console-settings-sheet">
      <SheetTitle>平台设置</SheetTitle>
      <SheetDescription>{session?.username} · {session?.admin ? '实例管理员' : owner ? '空间所有者' : '空间成员'}</SheetDescription>
      <Separator/>
      <nav className="settings-destinations" aria-label="平台设置">
        {allowed.map(([key, title]) => <Button key={key} variant="ghost" asChild>
          <a href={'#' + key} aria-current={current === key ? 'page' : undefined} onClick={() => setOpen(false)}>{title}</a>
        </Button>)}
        {session?.admin && <Button variant="ghost" asChild><a href="/api/docs">API 文档</a></Button>}
      </nav>
      <Separator/><AppearanceSelect/>
    </SheetContent>
  </Sheet>;
}

function ConsoleChrome({ model, controls, nav }) {
  const { session, owner, workspace, current, drawer } = model;
  const role = session?.admin ? '管理员' : owner ? '所有者' : '成员';
  return <>
    <a className="brand" id="console-brand" href={location.pathname} aria-label="Orchpact 工作台首页">
      <span className="console-wordmark">
        <img data-wordmark="light" src="/static/brand/orchpact-logo.svg" width="322" height="80" alt="Orchpact"/>
        <img data-wordmark="dark" src="/static/brand/orchpact-logo-dark.svg" width="322" height="80" alt="Orchpact"/>
      </span>
      <img className="compact-mark" src="/static/brand/orchpact-icon.svg" width="36" height="36" alt=""/>
    </a>
    <label className="workspace-control">
      <Icon name="space"/><span>工作空间</span>
      <select id="workspace" aria-label="当前工作空间" value={workspace} onChange={event => controls.changeSpace(event.target.value)}>
        {session?.workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
    </label>
    <div className="toolbar">
      {session && <Button asChild variant="ghost" className="account-button">
        <a href="#account" aria-label={session.username + '，账号与积分'} aria-current={current === 'account' ? 'page' : undefined}>
          <Icon name="account"/><span id="console-identity">{session.username}</span><Badge variant="secondary" className="role-label">{role}</Badge>
        </a>
      </Button>}
      {session?.admin && <Button asChild variant="ghost" size="icon" className="toolbar-api">
        <a href="/api/docs" aria-label="API 文档" title="API 文档"><Icon name="api"/></a>
      </Button>}
      <Button id="refresh" type="button" variant="ghost" size="icon" aria-label="刷新当前页面" title="刷新当前页面" onClick={controls.refresh}><Icon name="refresh"/></Button>
      <Button id="logout" type="button" variant="ghost" size="icon" aria-label="退出账号" title="退出账号" onClick={controls.logout}><Icon name="exit"/></Button>
    </div>
    {createPortal(<>
      {destinations.map(([key, title]) => <Button key={key} asChild variant="ghost">
        <a href={'#' + key} aria-current={current === key ? 'page' : undefined}
          aria-controls={key === 'canvas' ? undefined : 'resource-drawer'} aria-expanded={key === 'canvas' ? undefined : drawer === key}
          onClick={key === 'canvas' && drawer ? event => { event.preventDefault(); controls.closeDrawer(); } : undefined}>
          <Icon name={key}/><span>{title}</span>
        </a>
      </Button>)}
      <div className="platform-entry"><Separator/><PlatformSettings session={session} owner={owner} current={current}/></div>
    </>, nav)}
  </>;
}

export function mountConsole(controls) {
  const root = createRoot(document.querySelector('.console-bar'));
  const nav = document.querySelector('.console-nav');
  let model = { session: null, owner: false, workspace: '', current: 'canvas', drawer: null };
  function update(changes) {
    model = { ...model, ...changes };
    flushSync(() => root.render(<ConsoleChrome model={model} controls={controls} nav={nav}/>));
  }
  update({});
  return update;
}

// Form fields are mounted by each workflow action; the dialog owns focus and
// dismissal while those fields retain their live validation and draft input.
let editorRoot;
export function openEditor({ title, caption, onDismiss }) {
  editorRoot ??= createRoot(document.querySelector('#editor-host'));
  const trigger = document.activeElement;
  flushSync(() => editorRoot.render(<Dialog open onOpenChange={open => { if (!open) onDismiss(); }}>
    <DialogContent id="editor" className="console-editor" showCloseButton={false} aria-describedby={undefined}
      onOpenAutoFocus={event => event.preventDefault()}
      onCloseAutoFocus={event => {
        event.preventDefault();
        if (trigger?.isConnected) trigger.focus({preventScroll:true});
      }}>
      <form id="edit-form">
        <div className="dialog-head">
          <DialogTitle><span id="edit-title">{title}</span></DialogTitle>
          <Button type="button" variant="ghost" id="close-editor" aria-label="关闭" onClick={onDismiss}>×</Button>
        </div>
        <div id="edit-fields"/>
        <p id="edit-error" role="alert"/>
        <div className="actions"><Button type="submit" id="save-editor">{caption}</Button></div>
      </form>
    </DialogContent>
  </Dialog>));
  return () => flushSync(() => editorRoot.render(null));
}
