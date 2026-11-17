export function mountAccountSettings(root,{embedded=false}={}){
let disposed=false;const requests=new AbortController(),host=root===document?document.body:root;
'use strict';
const $=s=>root.querySelector(s),q=new URLSearchParams(location.search);
const isProductRoot=document.body.dataset.productRoot==='true';
const requested=q.get('return')||'/';
const returnPath=requested.startsWith('/')&&!requested.startsWith('//')&&!/[\\\\\x00-\x1f]/.test(requested)?requested:'/';
let me,selected;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function tell(message){if(disposed)return;$('#message').textContent=message;clearTimeout(tell.timer);tell.timer=setTimeout(()=>$('#message').textContent='',6000);}
async function api(path,method='GET',body,headers={}){const r=await fetch('/api/v1/'+path,{method,credentials:'same-origin',signal:requests.signal,headers:{'Content-Type':'application/json',...headers},...(body===undefined?{}:{body:JSON.stringify(body)})});const v=await r.json();if(disposed)throw new DOMException('Page disposed','AbortError');if(!r.ok)throw Object.assign(Error(v.detail||'请求失败'),{status:r.status});return v;}
function changed(){localStorage.setItem('account.changed',crypto.randomUUID());}
async function run(fn){try{await fn();}catch(e){if(e.name!=='AbortError')tell(e.message);}}
const loginForm=$('#login-form'),loginSubmit=$('#login-submit'),inviteField=$('#invite-field');
let registering=false;
function loginMode(register){
 if(!inviteField)return;
 registering=register;
 inviteField.hidden=!register;
 loginForm.elements.invitation.disabled=!register;
 loginForm.elements.invitation.required=register;
 loginForm.elements.password.autocomplete=register?'new-password':'current-password';
 $('#login-title').textContent=register?'创建 Orchpact 账号':'回到你的空间';
 loginSubmit.textContent=register?'注册并继续 ↗':'登录并继续 ↗';
 for(const [tab,active] of [[$('#login-tab'),!register],[$('#register-tab'),register]]){
  tab.classList.toggle('active',active);
  tab.setAttribute('aria-pressed',String(active));
 }
}
if(inviteField){
 $('#login-tab').onclick=()=>{if(!loginSubmit.disabled)loginMode(false);};
 $('#register-tab').onclick=()=>{if(!loginSubmit.disabled)loginMode(true);};
}
loginForm.onsubmit=e=>{
 e.preventDefault();
 if(loginSubmit.disabled)return;
 run(async()=>{
  const data=Object.fromEntries(new FormData(loginForm));
  if(registering)data.invitation=data.invitation.trim();
  loginSubmit.disabled=true;
  try{
   await api(registering?'auth/register':'auth/login','POST',data);
   loginForm.reset();loginMode(false);changed();await load();
  }finally{loginSubmit.disabled=false;}
 });
};
const adminForm=$('#admin-form');if(adminForm)adminForm.onsubmit=e=>{e.preventDefault();run(async()=>{await api('session','POST',Object.fromEntries(new FormData(e.target)));e.target.reset();changed();await load();});};
$('#logout').onclick=()=>run(async()=>{await api('session','DELETE');changed();me=null;if(embedded){location.assign('/admin/');return;}globalThis.OrchpactShell?.unmount();$('#workspace-panel').hidden=true;$('#login-panel').hidden=false;});
$('#create-form').onsubmit=e=>{e.preventDefault();run(async()=>{const space=await api('workspaces','POST',Object.fromEntries(new FormData(e.target)));e.target.reset();selected=space.id;await load();});};
$('#join-form').onsubmit=e=>{e.preventDefault();run(async()=>{const joined=await api('workspaces/join','POST',Object.fromEntries(new FormData(e.target)));e.target.reset();selected=joined.workspace_id;await load();tell('已加入协作空间');});};
function showInvitation(value){$('#invite-code').value=value.code;$('#invite-dialog').showModal();}

$('#copy-code').onclick=()=>run(async()=>{await navigator.clipboard.writeText($('#invite-code').value);tell('邀请码已复制');});$('#close-invite').onclick=()=>$('#invite-dialog').close();
async function members(id){selected=id;const space=me.workspaces.find(w=>w.id===id),owner=space.owner_id===me.user_id||me.admin,rows=await api('workspaces/'+id+'/members');
 $('#members').innerHTML=`<hr><h3>${esc(space.name)} · 成员</h3>${rows.map(u=>`<div class="member"><span>${esc(u.username)} · ${u.role==='owner'?'所有者':'成员'}</span>${u.role!=='owner'&&(owner||u.id===me.user_id)?`<button data-remove="${esc(u.id)}">${u.id===me.user_id?'退出空间':'移除成员'}</button>`:''}</div>`).join('')}`;
 root.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>run(async()=>{await api('workspaces/'+id+'/members/'+b.dataset.remove,'DELETE');await load();if(me.workspaces.some(w=>w.id===id))await members(id);}));
 if(owner&&!space.personal){const pending=await api('workspaces/'+id+'/invitations');const div=document.createElement('div');div.innerHTML=pending.filter(i=>!i.revoked&&!i.used_by&&i.expires*1000>Date.now()).map(i=>`<div class="member"><span>待接受邀请 · ${new Date(i.expires*1000).toLocaleDateString()}</span><button data-revoke="${esc(i.id)}">撤销</button></div>`).join('');$('#members').append(div);div.querySelectorAll('[data-revoke]').forEach(b=>b.onclick=()=>run(async()=>{await api('workspaces/'+id+'/invitations/'+b.dataset.revoke,'DELETE');await members(id);}));}
}
async function load(){try{me=await api('auth/me');}catch(error){if(disposed)return;if(embedded){if(error.status===401||error.status===403)location.assign('/admin/');else tell(error.message);return;}me=null;globalThis.OrchpactShell?.unmount();$('#login-panel').hidden=false;$('#workspace-panel').hidden=true;return;}
 $('#login-panel').hidden=true;$('#workspace-panel').hidden=false;$('#identity').textContent=me.username+(me.admin?' / 管理员':'');$('#create-form').hidden=!me.user_id;
 if(me.admin&&!isProductRoot&&!embedded){const link=document.createElement('a');link.href='/admin/';link.textContent='← 管理台';$('#workspace-panel').prepend(link);$('#workspace-panel>h2').textContent='账号与工作空间';}
 renderCreditSummary();
 $('#spaces').innerHTML=(me.admin?`<div class="space"><div>管理空间<small>仅管理员可见</small></div><div class="space-actions"><a href="/admin/#projects">打开</a></div></div>`:'')+me.workspaces.map(w=>`<div class="space"><div class="space-title">${esc(w.name)}<small>${w.personal?'私人 · 仅你可见':w.role==='owner'?'协作 · 你是所有者':'协作 · 已加入'}</small></div><div class="space-actions"><a href="${returnPath}?workspace=${encodeURIComponent(w.id)}">打开</a><button data-members="${esc(w.id)}">成员</button>${!w.personal&&(w.role==='owner'||me.admin)?`<button data-invite="${esc(w.id)}">邀请</button>`:''}</div></div>`).join('');
 root.querySelectorAll('[data-members]').forEach(b=>b.onclick=()=>run(()=>members(b.dataset.members)));
 root.querySelectorAll('[data-invite]').forEach(b=>b.onclick=()=>run(async()=>{showInvitation(await api('workspaces/'+b.dataset.invite+'/invitations','POST',{}));await members(b.dataset.invite);}));
 $('#members').replaceChildren();$('#admin-tools').hidden=!me.admin;if(me.admin){const users=await api('admin/users');$('#users').innerHTML=users.map(u=>`<div class="member"><span>${esc(u.username)} · 可用 ${amount(u.credits.available)} 积分</span><button data-credits="${esc(u.id)}" data-name="${esc(u.username)}">积分管理</button><button data-user="${esc(u.id)}" data-disabled="${u.disabled?0:1}">${u.disabled?'启用账号':'停用账号'}</button></div>`).join('');root.querySelectorAll('[data-credits]').forEach(b=>b.onclick=()=>run(()=>showCredits(b.dataset.credits,b.dataset.name)));root.querySelectorAll('[data-user]').forEach(b=>b.onclick=()=>run(async()=>{await api('admin/users/'+b.dataset.user,'PATCH',{disabled:!!Number(b.dataset.disabled)});await load();}));}
}

function amount(value){return Number(value).toLocaleString('zh-CN',{maximumFractionDigits:6});}
function renderCreditSummary(){
 $('#credit-summary').hidden=!me.credits;if(!me.credits)return;
 $('#credit-summary').innerHTML=`<h3>可用积分 ${amount(me.credits.available)}</h3><p class="note">余额 ${amount(me.credits.balance)} · 调用预留 ${amount(me.credits.held)}。任务消耗发起者的积分。</p><button id="my-credits">查看积分流水</button>`;
 $('#my-credits').onclick=()=>run(()=>showCredits(me.user_id,me.username));
}
async function showCredits(user,name){
 const account=await api(me.admin?'admin/users/'+user+'/credits':'auth/credits'),w=account.credits;
 $('#credit-title').textContent=name+' · 积分账户';
 const names={reserve:'调用预留',settlement:'调用结算',adjustment:'管理员调整'};
 $('#credit-body').innerHTML=`<h3>可用 ${amount(w.available)} 积分</h3><p class="note">余额 ${amount(w.balance)} · 预留 ${amount(w.held)}。缺少用量的调用保留预留积分，核定后释放差额。</p>${me.admin?'<form id="credit-adjust"><label>增减积分（正数增加、负数扣减）<input name="delta" type="number" step="0.000001" min="-1000000000" max="1000000000" required></label><label>调整原因<input name="reason" maxlength="500" required></label><button type="submit">保存积分调整</button></form>':''}<h3>未结算调用</h3>${account.pending.map(p=>`<div class="credit-entry"><p>${esc(p.request_id)} · ${esc(p.status)} · 预留 ${amount(p.reserved)}</p>${me.admin&&p.status!=='started'?`<form data-settle="${esc(p.request_id)}"><label>核定扣除积分（0表示全部释放）<input name="points" type="number" min="0" max="1000000000" step="0.000001" required></label><label>核定原因<input name="reason" maxlength="500" required></label><button type="submit">核定并结算</button></form>`:''}</div>`).join('')||'<p class="note">暂无</p>'}<h3>最近100条流水</h3>${account.ledger.map(r=>`<div class="credit-entry"><b>${esc(names[r.kind]||r.kind)} · ${r.delta>0?'+':''}${amount(r.delta)} 积分</b><p>${esc(r.reason)}</p><small>${new Date(r.created*1000).toLocaleString('zh-CN')} · 余额 ${amount(r.balance)} · 预留 ${amount(r.held)}${r.request_id?'<br>调用 '+esc(r.request_id):''}</small></div>`).join('')}`;
 const form=$('#credit-adjust');if(form)form.onsubmit=event=>{event.preventDefault();run(async()=>{
  const button=form.querySelector('button');if(button.disabled)return;button.disabled=true;
  try{const raw=Object.fromEntries(new FormData(form)),payload={delta:Number(raw.delta),reason:raw.reason},signature=JSON.stringify(payload);
   if(form.dataset.payload!==signature){form.dataset.payload=signature;form.dataset.key=crypto.randomUUID();}
   await api('admin/users/'+user+'/credits','POST',payload,{'Idempotency-Key':form.dataset.key});await load();await showCredits(user,name);tell('积分已调整');
  }finally{button.disabled=false;}
 });};
 root.querySelectorAll('[data-settle]').forEach(form=>form.onsubmit=event=>{event.preventDefault();run(async()=>{
  const button=form.querySelector('button');if(button.disabled)return;button.disabled=true;
  try{const raw=Object.fromEntries(new FormData(form));await api('admin/credit-requests/'+form.dataset.settle+'/settle','POST',{points:Number(raw.points),reason:raw.reason});await load();await showCredits(user,name);tell('调用积分已核定');}finally{button.disabled=false;}
 });});
 if(!$('#credit-dialog').open)$('#credit-dialog').showModal();
}
const creditSummary=document.createElement('section');creditSummary.id='credit-summary';creditSummary.hidden=true;$('#spaces').before(creditSummary);
const creditDialog=document.createElement('dialog');creditDialog.id='credit-dialog';creditDialog.innerHTML='<h2 id="credit-title">积分账户</h2><div id="credit-body"></div><button id="close-credits">关闭</button>';host.append(creditDialog);
$('#close-credits').onclick=()=>creditDialog.close();
run(load);

return {refresh:()=>run(load),dispose(){disposed=true;requests.abort();clearTimeout(tell.timer);root.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());creditDialog.remove();}};
}
if(typeof document!=='undefined'&&document.querySelector('main[data-account-page]'))mountAccountSettings(document);
