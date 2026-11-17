/* Shared account boot: product scripts start only after identity and space are resolved. */
(async()=>{
 'use strict';
 const script=document.currentScript,rawFetch=window.fetch.bind(window),page=location.pathname;
 const entry=script.dataset.scripts?JSON.parse(script.dataset.scripts):[];
 const accountUrl='/account/?return='+encodeURIComponent(page);
 async function json(path,options={}){const r=await rawFetch('/api/v1/'+path,{credentials:'same-origin',...options});const v=await r.json();if(!r.ok)throw Object.assign(Error(v.detail||'请求失败'),{status:r.status});return v;}
 let me;
 try{me=await json('auth/me');}catch(e){if(e.status===401){location.replace(accountUrl);return;}document.body.textContent='暂时无法连接，请刷新重试。';return;}
 const key='workspace.selection:'+me.user_id;
 const params=new URLSearchParams(location.search);let selected=params.get('workspace')||localStorage.getItem(key)||me.workspace_id||'';
 if(selected&&!me.workspaces.some(w=>w.id===selected))selected=me.workspace_id||'';
 localStorage.setItem(key,selected);
 const prefix='scope:'+ (me.user_id||'admin')+':'+(selected||'admin')+':';
 function store(storage){return {getItem:k=>storage.getItem(prefix+k),setItem:(k,v)=>storage.setItem(prefix+k,v),removeItem:k=>storage.removeItem(prefix+k)};}
 window.WorkspaceContext={me,workspace:selected,local:store(localStorage),session:store(sessionStorage)};
 window.fetch=(input,options={})=>{
  const url=new URL(typeof input==='string'?input:input.url,location.href);
  if(url.origin===location.origin&&url.pathname.startsWith('/api/v1/')){
   const headers=new Headers(options.headers||((typeof input!=='string')?input.headers:undefined));
   if(selected)headers.set('X-Workspace-Id',selected);
   options={...options,headers};
  }
  return rawFetch(input,options);
 };
 // Browser state is scoped by identity and workspace.
 if(me.admin&&!selected){window.WorkspaceContext.local=localStorage;window.WorkspaceContext.session=sessionStorage;}
 const bar=document.createElement('div');bar.className='workspace-bar';
 const clearance=document.createElement('div');clearance.className='workspace-bar-clearance';clearance.setAttribute('aria-hidden','true');document.body.append(clearance);
 const label=document.createElement('span');label.textContent='WORKSPACE';
 const select=document.createElement('select');select.setAttribute('aria-label','当前工作空间');
 if(me.admin){select.add(new Option('管理空间',''));}
 for(const w of me.workspaces)select.add(new Option(w.name+(w.personal?' · 私人':''),w.id));
 select.value=selected;
 select.onchange=()=>{const target=new URL(location.href);target.searchParams.set('workspace',select.value);location.assign(target);};
 const manage=document.createElement('a');manage.href=accountUrl;manage.textContent=me.username+' · 管理空间';
 bar.append(label,select,manage);document.body.prepend(bar);
 const balance=document.createElement('a');balance.href=accountUrl;balance.setAttribute('aria-label','查看个人积分余额');
 function showBalance(value){balance.hidden=!value;if(value)balance.textContent='可用 '+Number(value.available).toLocaleString('zh-CN',{maximumFractionDigits:6})+' 积分';}
 showBalance(me.credits);bar.append(balance);
 await new Promise((resolve,reject)=>{const node=document.createElement('script');node.src='/static/account/credits.js';node.onload=resolve;node.onerror=reject;document.head.append(node);});
 const info=document.createElement('details');info.className='workspace-credit-info';
 const summary=document.createElement('summary');summary.textContent='积分规则';const explanation=document.createElement('p');info.append(summary,explanation);bar.append(info);
 const credits=new window.ApplicationCredits(window.fetch.bind(window),value=>{showBalance(value.credits);window.WorkspaceContext.me=value;if(window.WorkspaceContext.credits)explanation.textContent=window.WorkspaceContext.credits.notice();});
 window.WorkspaceContext.credits=credits;credits.update(me);
 setInterval(()=>{if(!document.hidden)credits.refresh().catch(()=>{});},15000);
 const style=document.createElement('link');style.rel='stylesheet';style.href='/static/account/workspace.css';document.head.append(style);
 window.addEventListener('storage',e=>{if(e.key==='account.changed')location.reload();});
 document.addEventListener('visibilitychange',async()=>{if(!document.hidden){try{const next=await json('auth/me');if(next.user_id!==me.user_id||next.admin!==me.admin)location.reload();}catch{location.replace(accountUrl);}}});
 if(!me.admin){
  for(const input of document.querySelectorAll('#token,#api-token')){
   const label=input.closest('label')||document.querySelector('label[for="'+input.id+'"]');if(label){label.hidden=true;label.style.display='none';}
   input.hidden=true;input.style.display='none';
   input.disabled=true;
   const link=document.createElement('a');link.href=accountUrl;link.textContent='登录账号或管理工作空间';
   input.parentElement.append(link);
  }
 }
 for(const src of entry){await new Promise((resolve,reject)=>{const node=document.createElement('script');node.src=src;node.onload=resolve;node.onerror=reject;document.body.append(node);});}
})();
