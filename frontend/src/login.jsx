import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { createRoot } from 'react-dom/client';
import { Button, Input } from './components/ui';
import { request, destination } from '/static/account/login.js';

const form = document.querySelector('#login-form');
const title = { login: '登录工作台', register: '受邀注册', setup: '创建首个账号', unavailable: '等待实例初始化' };
const help = {
  setup: '首个账号是此实例唯一的管理员。请设置至少 12 个字符的密码。',
  register: '使用管理员提供的邀请码注册，密码至少 12 个字符。',
  unavailable: '在实例本机运行 orchpact setup-admin <账号>，完成后刷新此页。',
};

function AccountEntry() {
  const [mode, setMode] = useState(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const usable = ['login', 'register', 'setup'].includes(mode);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        try {
          const account = await request('me');
          if (active) location.replace(destination(account.admin));
          return;
        } catch (error) {
          if (error.status !== 401) throw error;
        }
        const setup = await request('setup');
        if (active) setMode(setup.required ? (setup.available ? 'setup' : 'unavailable') : 'login');
      } catch {
        if (active) setMessage('暂时无法读取会话，请刷新重试。');
      } finally {
        if (active) document.querySelector('#account-login').setAttribute('aria-busy', 'false');
      }
    }
    load();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    form.hidden = !usable;
    async function submit(event) {
      event.preventDefault();
      if (!usable || pending.current) return;
      pending.current = true;
      setBusy(true);
      setMessage('');
      try {
        const data = Object.fromEntries(new FormData(form));
        if (mode === 'register') data.invitation = data.invitation.trim();
        await request(mode, data);
        form.reset();
        const account = await request('me');
        try { localStorage.setItem('account.changed', crypto.randomUUID()); } catch { /* Storage is optional. */ }
        location.replace(destination(account.admin));
      } catch (error) {
        setMessage(error.message);
      } finally {
        pending.current = false;
        setBusy(false);
      }
    }
    form.addEventListener('submit', submit);
    return () => form.removeEventListener('submit', submit);
  }, [mode, usable]);

  return <>
    <h1 id="login-title">{title[mode] || '登录工作台'}</h1>
    {help[mode] && <p id="login-help" className="entry-hint">{help[mode]}</p>}
    {!mode && !message && <p className="entry-hint">正在读取实例状态…</p>}
    {['login', 'register'].includes(mode) && <div id="account-modes" role="group" aria-label="个人账号入口" className="entry-modes">
      {[['login', '登录'], ['register', '受邀注册']].map(([value, text]) =>
        <Button key={value} type="button" id={value + '-tab'} variant="ghost" disabled={busy} aria-pressed={mode === value}
          onClick={() => { setMode(value); setMessage(''); }}>{text}</Button>
      )}
    </div>}
    {usable && createPortal(<>
      <label htmlFor="entry-password" className="entry-label">密码</label>
      <Input id="entry-password" name="password" type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={12} maxLength={128} required/>
      {mode === 'register' && <div id="invite-field">
        <label htmlFor="entry-invitation" className="entry-label">注册邀请码</label>
        <Input id="entry-invitation" name="invitation" autoComplete="off" minLength={20} maxLength={200} required/>
      </div>}
    </>, document.querySelector('#login-fields'))}
    {usable && createPortal(<Button id="login-submit" type="submit" disabled={busy} className="entry-submit">
      {busy ? '正在提交…' : mode === 'setup' ? '创建账号并进入' : mode === 'register' ? '注册' : '登录'}
    </Button>, document.querySelector('#login-action'))}
    {message && createPortal(<p className="entry-error" role="alert" id="login-message">{message}</p>, document.querySelector('#login-feedback'))}
  </>;
}
createRoot(document.querySelector('#login-controls')).render(<AccountEntry/>);
