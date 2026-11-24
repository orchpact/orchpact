'use strict';
const $=s=>document.querySelector(s), content=$('#content');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let me,revision=0,pageController,saveAction,savedSignature,savedKey,editWorkspace;
const names={queued:'等待执行',running:'执行中',waiting:'等待依赖或决定',awaiting_approval:'外部写操作待审批',rejected:'已拒绝',completed:'已完成',partial:'部分完成',failed:'失败',interrupted:'执行中断',cancelled:'已取消',accepted:'已验收',pending:'待验收',waived:'缺口已记录'};
function tell(value,error=false){$('#notice').textContent=value;$('#notice').className=error?'error':'';}
async function api(path,{method='GET',value,signal,headers={}}={}){
 const response=await fetch('/api/v1/'+path,{method,credentials:'same-origin',signal,headers:{'Content-Type':'application/json','X-Workspace-Id':$('#workspace').value,...headers},...(value===undefined?{}:{body:JSON.stringify(value)})});
 const data=await response.json();if(!response.ok){if(response.status===401)location.assign('/admin/');const detail=Array.isArray(data.detail)?data.detail.map(item=>item.msg).join('；'):data.detail;throw Object.assign(Error(detail||'请求失败'),{status:response.status});}return data;
}
function heading(title,description,action=''){return `<div class="heading"><div><h1>${esc(title)}</h1><p class="muted">${esc(description)}</p></div>${action}</div>`;}
function empty(text){return `<div class="panel muted">${esc(text)}</div>`;}
function showToolApprovals(run,signal,current){
 const approvals=run.tool_approvals||[];
 if(!approvals.length)return;
 const labels={pending:'待审批',approved:'已批准，等待执行',used:'已用于一次调用',rejected:'已拒绝',stale:'已失效'};
 const section=document.createElement('section');
 section.className='panel';
 section.innerHTML='<h2>外部写操作审批</h2>'+approvals.map(a=>`<div class="task">
  <div class="row"><strong>${esc(a.logical_tool)} · ${esc(a.endpoint_ref)}</strong><span>${esc(labels[a.status])}</span></div>
  <p class="muted">工作项 ${esc(a.task_id)} · 代次 ${esc(a.generation)} · 发起账号 ${esc(run.payer_user_id)}</p>
  <button type="button" data-tool-approval="${esc(a.id)}">查看目标与参数</button>
  <div data-approval-detail></div></div>`).join('');
 content.querySelector('.panel').after(section);
 for(const button of section.querySelectorAll('[data-tool-approval]'))button.onclick=async()=>{
  const path='runs/'+encodeURIComponent(run.id)+'/tool-approvals/'+encodeURIComponent(button.dataset.toolApproval);
  const detail=button.nextElementSibling;
  button.disabled=true;
  try{
   const a=await api(path,{signal});
   if(!current())return;
   detail.innerHTML=`<p>目标 ${esc(a.endpoint_ref)} · 实现版本 ${esc(a.implementation_version)}</p>
    <pre>${esc(JSON.stringify(a.arguments,null,2))}</pre><p>${esc(labels[a.status])}</p>
    ${a.status==='pending'?'<div class="actions"><button type="button" data-verdict="approve">批准并排队</button><button type="button" data-verdict="reject">拒绝调用</button></div>':''}`;
   for(const verdict of detail.querySelectorAll('[data-verdict]'))verdict.onclick=async()=>{
    if(!current())return;
    const actions=detail.querySelectorAll('[data-verdict]');
    for(const action of actions)action.disabled=true;
    try{
     await api(path+'/decision',{method:'POST',value:{decision:verdict.dataset.verdict,approval_digest:a.approval_digest}});
     if(current())await render();
    }catch(error){if(current())tell(error.message,true);}
    finally{for(const action of actions)action.disabled=false;}
   };
  }catch(error){if(current())tell(error.message,true);}
  finally{button.disabled=false;}
 };
}
function edit(title,value,help,action){$('#edit-title').textContent=title;$('#edit-help').textContent=help;$('#edit-json').value=JSON.stringify(value,null,2);$('#edit-error').textContent='';saveAction=action;editWorkspace=$('#workspace').value;savedSignature=null;savedKey=null;$('#editor').showModal();}
$('#close-editor').onclick=()=>$('#editor').close();
$('#edit-form').onsubmit=async event=>{event.preventDefault();const button=$('#save-editor');if(button.disabled)return;button.disabled=true;try{if(editWorkspace!==$('#workspace').value)throw Error('当前空间已切换，请保留内容并在原空间重新提交');const value=JSON.parse($('#edit-json').value);const signature=JSON.stringify(value);if(savedSignature!==signature){savedSignature=signature;savedKey=crypto.randomUUID();}const next=await saveAction(value,savedKey);$('#editor').close();tell('已保存');await render();if(typeof next==='function'&&editWorkspace===$('#workspace').value)next();}catch(error){$('#edit-error').textContent=error.message;}finally{button.disabled=false;}};
async function render(){
 const token=++revision;pageController?.abort();pageController=new AbortController();const signal=pageController.signal;
 const hash=location.hash.slice(1)||'overview', [page,id]=hash.split('/');
 if(['organizations','policies'].includes(page))$('#advanced-navigation').open=true;
 for(const a of document.querySelectorAll('nav a'))a.setAttribute('aria-current',a.hash==='#'+page?'page':'false');
 content.innerHTML='<p class="muted">正在读取…</p>';
 const current=()=>token===revision&&!signal.aborted;
 try{
  if(page==='providers'){
   const result=await api('admin/configuration',{signal});if(!current())return;
   content.innerHTML=heading('提供者配置','凭据使用环境变量或只写入的受保护引用；历史调用保留自己的价格快照。','<button id="configure" class="primary">编辑配置</button>')+`<div class="table-scroll panel"><table><thead><tr><th>提供者</th><th>能力</th><th>状态</th><th>模型</th><th>价格</th></tr></thead><tbody>${result.value.providers.map(p=>`<tr><td>${esc(p.label)}<br><small>${esc(p.id)}</small></td><td>${esc(p.category)}</td><td>${p.available?'可用':p.enabled?'待配置凭据':'停用'}</td><td>${esc(p.model)}</td><td>${esc(JSON.stringify(p.pricing))}</td></tr>`).join('')}</tbody></table></div>`;
   $('#configure').onclick=()=>edit('提供者与资源配置',result.value,'secret 字段仅用于提交新密钥，保存后不会返回明文。',value=>api('admin/configuration',{method:'PUT',value:{revision:result.revision,value}}));return;
  }
  if(page==='policies'){
   const scope='workspace:'+$('#workspace').value,result=await api('policies/'+encodeURIComponent(scope),{signal});if(!current())return;
   content.innerHTML=heading('治理规则','规则由平台执行。组织可以自行配置专业审校职责，无预置部门。','<button id="policy" class="primary">编辑空间规则</button>')+`<div class="panel"><p>范围 ${esc(scope)} · revision ${result.revision}</p><pre>${esc(JSON.stringify(result.policy,null,2))}</pre></div>`;
   $('#policy').onclick=()=>edit('空间治理规则',result.policy,'输入/交付契约按任务冻结，预算与授权在新行动前复查。',policy=>api('policies/'+encodeURIComponent(scope),{method:'PUT',value:{revision:result.revision,policy}}));return;
  }
  if(page==='workflows'){
   const [assets,templates,models]=await Promise.all([api('workflows',{signal}),api('workflow-templates',{signal}),api('models',{signal})]);if(!current())return;
   content.innerHTML=heading('工作流目录','定义与修订是独立资产；发布新修订不会升级当前空间已安装的版本。','<button id="new-workflow" class="primary">登记工作流</button>')+'<section class="panel template-creator" id="template-creator"></section>'+(assets.length?assets.map(w=>`<section class="panel"><div class="row"><h2>${esc(w.definition.name)}</h2><span class="badge">${esc(w.version)} · revision ${w.revision}</span></div><p>${esc(w.id)}</p><small>SHA-256 ${esc(w.sha256)}</small><div class="actions"><button data-publish="${esc(w.id)}">发布新修订</button><button data-install-workflow="${esc(w.id)}">预览与安装资源</button><button data-run-workflow="${esc(w.id)}">直接运行</button><a class="action-link" href="/api/v1/workflows/${esc(encodeURIComponent(w.id))}/definition?revision=${w.revision}">导出此修订 ↓</a></div></section>`).join(''):empty('先登记可复用定义，再预览资源、安装到当前空间并直接运行。'));
   OrchpactTemplateForm.mount($('#template-creator'),templates,{current,preview:(template,parameters)=>api('workflow-templates/'+encodeURIComponent(template.template_id)+'/preview',{method:'POST',value:{sha256:template.sha256,parameters},signal}),publish:definition=>edit('登记模板展开的工作流',definition,'这是普通工作流定义。保存只登记目录修订，不自动安装空间或发起执行。',value=>api('workflows',{method:'POST',value}))});
   $('#new-workflow').onclick=()=>edit('登记独立工作流',{format:'orchpact-workflow',id:'my-workflow',name:'我的工作流',version:'1.0.0',resources:{models:{reasoning:{capabilities:['text','json']}}},units:[{key:'entry',name:'处理组',purpose:'处理输入并交付约定结果',model:'reasoning'}],operations:{create:{unit:'entry',instruction:'交付 result.json',input_schema:{type:'object'},output_schema:{type:'object'},output_file:'result.json',execution_plan:{result_node:'deliver',nodes:[{key:'deliver',unit:'entry',description:'按契约交付 result.json',expected:'result.json',staff:{name:'交付执行者',role:'处理输入并交付约定结果',reason:'执行固定节点',capabilities:['text','json'],model:'reasoning'},outputs:{'result.json':{type:'object'}}}]}}}},'只登记固定计划定义，不自动安装空间或发起执行。请补充具体交付约束及必需验证。',value=>api('workflows',{method:'POST',value}));
   for(const b of content.querySelectorAll('[data-publish]'))b.onclick=()=>{const w=assets.find(w=>w.id===b.dataset.publish);edit('发布工作流新修订',w.definition,'已安装空间继续使用原修订。旧定义不可覆盖。',value=>{if(value.id!==w.id)throw Error('发布新修订不能更换工作流标识');return api('workflows?revision='+w.revision,{method:'POST',value});});};
   for(const b of content.querySelectorAll('[data-install-workflow]'))b.onclick=async()=>{
    const w=assets.find(w=>w.id===b.dataset.installWorkflow);let installed=null;
    try{installed=await api('workflows/'+encodeURIComponent(w.id)+'/installation',{signal});}catch(error){if(error.status!==404){tell(error.message,true);return;}}
    if(!current())return;
    const bindings=installed?.bindings||{models:Object.fromEntries(Object.entries(w.definition.resources?.models||{}).map(([slot,req])=>[slot,models.find(m=>req.capabilities.every(cap=>m.capabilities.includes(cap)))?.id||'']))};
    if(w.definition.resources?.tools&&!bindings.tools)bindings.tools=Object.fromEntries(Object.keys(w.definition.resources.tools).map(slot=>[slot,{endpoint_ref:'',implementation_version:''}]));
    edit('预览工作流资源',{workflow_revision:w.revision,bindings},'保存先验证资源并显示只读预览；确认安装才更新当前 Workspace。',async value=>{
     const preview=await api('workflows/'+encodeURIComponent(w.id)+'/preview',{method:'POST',value});
     return ()=>edit('确认安装资源',{...value,expected_revision:preview.installed_revision},JSON.stringify(preview,null,2),body=>api('workflows/'+encodeURIComponent(w.id)+'/install',{method:'POST',value:body}));
    });
   };
   for(const b of content.querySelectorAll('[data-run-workflow]'))b.onclick=()=>{
    const w=assets.find(w=>w.id===b.dataset.runWorkflow);
    edit('运行 '+w.definition.name,{operation:Object.keys(w.definition.operations)[0],input:{},context:{}},'直接使用当前 Workspace 已安装的固定修订与资源，由当前账号承担预算。',async(value,key)=>{const run=await api('workflows/'+encodeURIComponent(w.id)+'/runs',{method:'POST',value,headers:{'Idempotency-Key':key}});location.hash='projects/'+run.id;});
   };return;
  }
  const state=await api('state',{signal});if(!current())return;
  const attention=OrchpactAttention.items(state);
  if(page==='attention'){
   content.innerHTML=heading('待处理','当前空间最近 100 个项目中的待办；分类不改变任务、授权或账务状态。')+`<div class="cards">${['receipt','accounting','approval','review','question','execution'].map(kind=>`<div class="card"><div class="number">${attention.filter(x=>x.kind===kind).length}</div><small>${({receipt:'结果待确认',accounting:'费用待核定',approval:'外部写操作待审批',review:'待独立验收',question:'待答复',execution:'执行需处理'})[kind]}</small></div>`).join('')}</div>`+(attention.length?attention.map(item=>`<article class="panel"><div class="row"><h2>${esc(item.workflow_id||item.application_id||'历史执行')} · ${esc(item.operation)}</h2><span class="badge">${esc(item.label)}</span></div><p>${esc(item.reason)}</p><p class="muted">${esc(item.action)}</p><small>记录 ${esc(item.reference)}</small><div class="actions"><a class="action-link" href="#projects/${esc(encodeURIComponent(item.project_id))}">打开项目 →</a>${['receipt','accounting'].includes(item.kind)?'<a class="action-link" href="/api/v1/requests/'+esc(encodeURIComponent(item.reference))+'?workspace='+esc(encodeURIComponent($('#workspace').value))+'">查看调用回执</a>':''}${item.kind==='accounting'?'<a class="action-link" href="/account/">账号与积分 ↗</a>':''}</div></article>`).join(''):empty('最近项目没有需要处理的事项。正常执行与等待依赖不会列为人工待办。'));return;
  }
  if(page==='organizations'){
   OrchpactOrganizations.show(content,state,{api,render,tell,heading,empty,esc,workspace:()=>$('#workspace').value});
   return;
  }
  if(page==='projects'&&id){
   const run=state.projects.find(p=>p.id===id);if(!run){content.innerHTML=empty('当前空间未找到该项目。');return;}
   const grants=await api('runs/'+id+'/mandates',{signal});if(!current())return;
   content.innerHTML=heading(run.operation,'项目 '+run.id)+`<div class="panel"><div class="row"><span class="badge">${esc(names[run.status]||run.status)}</span><span>计划 revision ${run.revision}</span></div><p>已知用量折算 ${run.usage.points} 内部积分${run.usage.complete?'':' · 用量记录未完整'}</p><div class="actions"><button id="cancel-run">取消未完成工作</button><button id="amend-run">调整待执行计划</button><button id="mandate-run">授予项目权限</button></div>${run.questions.map(q=>`<form data-question="${esc(q.id)}"><label>${esc(q.content)}<textarea name="answer" maxlength="8000" required></textarea></label><button>回答并继续</button></form>`).join('')}</div><section class="panel"><h2>实际工作与交付</h2>${run.tasks.map(t=>`<div class="task"><div class="row"><strong>${esc(t.name)} ${t.node_key?'<small>'+esc(t.node_key)+'</small>':''}</strong><span>${esc(names[t.status]||t.status)}</span></div><p>${esc(t.description)}</p><small>${esc(names[t.review]||t.review)} · 第 ${t.turns} 次执行</small>${t.error?'<p class="error">'+esc(t.error)+'</p>':''}<div class="actions">${t.artifact_id?'<a href="/api/v1/artifacts/'+encodeURIComponent(t.artifact_id)+'/download?workspace='+encodeURIComponent($('#workspace').value)+'">下载交付 ↓</a><button data-review="'+esc(t.id)+'">验收此版本</button>':''}${run.retryable_tasks.includes(t.id)?'<button data-retry="'+esc(t.id)+'">按原付款人重试</button>':''}</div></div>`).join('')}</section><section class="panel"><h2>行动授权</h2>${grants.map(g=>`<div class="task"><p>${esc(g.subject)} · ${esc(g.actions.join(' / '))}</p><small>${esc(g.capabilities.join(' / '))} · ${g.revoked?'已撤销':'到期 '+esc(new Date(g.expires*1000).toLocaleString())}</small>${!g.revoked?'<p><button data-revoke-grant="'+esc(g.id)+'">撤销此授权及其转授</button></p>':''}</div>`).join('')}</section><section class="panel"><h2>调用记录</h2>${run.requests.map(r=>`<p>${esc(r.kind)} · ${esc(r.status)} · ${esc(OrchpactAttention.accountingLabel(r))} · <a href="/api/v1/requests/${esc(r.id)}?workspace=${encodeURIComponent($('#workspace').value)}">${esc(r.id)}</a></p>`).join('')||'<p class="muted">尚无外部调用</p>'}</section>${run.result?'<section class="panel"><h2>已验收结果</h2><pre>'+esc(JSON.stringify(run.result,null,2))+'</pre></section>':''}`;
   showToolApprovals(run,signal,current);
   for(const b of content.querySelectorAll('[data-revoke-grant]'))b.onclick=async()=>{try{await api('runs/'+id+'/mandates/'+b.dataset.revokeGrant,{method:'DELETE'});await render();}catch(e){tell(e.message,true);}};
   $('#cancel-run').onclick=async()=>{try{await api('runs/'+id+'/cancel',{method:'POST',value:{}});await render();}catch(e){tell(e.message,true);}};
   $('#amend-run').onclick=()=>edit('计划变更',{reason:'',changes:[],add_nodes:[]},'仅获授权用户可调整尚未开始的节点。验收检查、其输入和汇总路径不可取消；验收要求与依赖不可削弱。修改契约须发布新定义并发起新执行。',(value,key)=>api('runs/'+id+'/commands',{method:'POST',value:{key,action:'amend',revision:run.revision,payload:value}}));
   $('#mandate-run').onclick=()=>edit('项目授权',{subject:'user:',actions:['review'],capabilities:[],expires:Math.floor(Date.now()/1000)+3600,budget_points:null},'授权不能扩大签发者的范围或有效期。',value=>api('runs/'+id+'/mandates',{method:'POST',value}));
   for(const form of content.querySelectorAll('[data-question]'))form.onsubmit=async event=>{event.preventDefault();try{await api('questions/'+form.dataset.question+'/answers',{method:'POST',value:{content:form.elements.answer.value}});await render();}catch(e){tell(e.message,true);}};
   for(const b of content.querySelectorAll('[data-retry]'))b.onclick=async()=>{try{b.disabled=true;await api('work-items/'+b.dataset.retry+'/retry',{method:'POST',value:{},headers:{'Idempotency-Key':crypto.randomUUID()}});await render();}catch(e){tell(e.message,true);}finally{b.disabled=false;}};
   for(const b of content.querySelectorAll('[data-review]'))b.onclick=async()=>{try{const t=run.tasks.find(t=>t.id===b.dataset.review),meta=await api('artifacts/'+t.artifact_id);edit('验收具体产物版本',{artifact_id:t.artifact_id,sha256:meta.sha256,verdict:'accept',feedback:''},'填写实际核验依据。返工后的产物须重新验收。',(value,key)=>api('runs/'+id+'/commands',{method:'POST',value:{key,action:'review',payload:value}}));}catch(e){tell(e.message,true);}};return;
  }
  content.innerHTML=heading(page==='projects'?'项目执行':'工作总览','每份成果都有来源，每个行动都有权限，每次调用都有记录。')+`<div class="cards"><div class="card"><div class="number">${state.units.length}</div><small>当前空间组织</small></div><a class="card" href="#attention"><div class="number">${attention.length}</div><small>最近项目待处理事项 →</small></a><div class="card"><div class="number">${state.projects.length}</div><small>最近项目</small></div><div class="card"><div class="number">${state.projects.filter(p=>['running','queued','waiting'].includes(p.status)).length}</div><small>执行中或等待决定</small></div></div>`+(state.projects.length?`<div class="table-scroll panel"><table><thead><tr><th>项目</th><th>状态</th><th>进度</th><th>用量</th></tr></thead><tbody>${state.projects.map(p=>`<tr><td><a href="#projects/${esc(p.id)}">${esc(p.workflow?.id||p.application_id||'历史执行')} · ${esc(p.operation)}</a></td><td>${esc(names[p.status]||p.status)}</td><td>${p.tasks.filter(t=>t.status==='completed').length}/${p.tasks.length}</td><td>${p.usage.points} 积分${p.usage.complete?'':'（用量未完整）'}</td></tr>`).join('')}</tbody></table></div>`:empty('尚无项目。先配置提供者与额度，再登记工作流并预览安装。'));
 }catch(error){if(error.name==='AbortError'||!current())return;content.innerHTML=empty(error.message);tell(error.message,true);}
}
$('#refresh').onclick=()=>render();$('#workspace').onchange=()=>{sessionStorage.setItem('orchpact.admin.workspace',$('#workspace').value);render();};$('#logout').onclick=async()=>{await api('session',{method:'DELETE'});location.assign('/admin/');};window.addEventListener('hashchange',()=>render());
(async()=>{try{me=await api('auth/me');$('#workspace').innerHTML=me.workspaces.map(w=>`<option value="${esc(w.id)}">${esc(w.name)}</option>`).join('');const saved=sessionStorage.getItem('orchpact.admin.workspace');$('#workspace').value=me.workspaces.some(w=>w.id===saved)?saved:me.workspace_id;await render();}catch(e){tell(e.message,true);}})();
