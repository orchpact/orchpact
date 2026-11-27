import {mountRunCanvas} from './run-canvas.js';

const encode=encodeURIComponent;
const lastFocus=new WeakMap();
const kinds={
  receipt:'结果待确认',accounting:'费用待核定',approval:'外部写操作待审批',
  review:'待独立验收',question:'待答复',execution:'执行需处理',
};

function formatTime(value){
  return value==null?'—':new Date(value*1000).toLocaleString('zh-CN',{hour12:false});
}
const tones = {
  running:'info', started:'info', approved:'info', accepted:'success', PASS:'success',
  awaiting_approval:'warning', partial:'warning', waived:'warning', pending:'warning',
  unknown:'warning', INCONCLUSIVE:'warning', rejected:'danger', failed:'danger', interrupted:'danger', FAIL:'danger',
};
function badge(ui,status,caption){
  return '<span class="badge" data-tone="'+(tones[status]||'neutral')+'">'+ui.esc(caption||ui.label[status]||status)+'</span>';
}
function pageLink(route, params, next){
  if(!next&&!params.has('cursor'))return '';
  const query=new URLSearchParams(params);
  query.delete('cursor');
  const first='#'+route+(query.size?'?'+query.toString().replaceAll('&','&amp;'):'');
  if(!next)return '<a class="action-link" href="'+first+'">回到首批结果</a>';
  query.set('cursor',next);
  return '<div class="actions"><a class="action-link" href="#'+route+'?'+query.toString().replaceAll('&','&amp;')+'">下一页 →</a>'
    +(params.has('cursor')?'<a class="action-link" href="'+first+'">回到首批结果</a>':'')+'</div>';
}
function displayId(value){return value?.slice(0,8)||'—';}

function runTable(ui,items){
  const {esc,empty}=ui;
  if(!items.length)return empty('没有符合条件的运行记录。');
  return '<div class="panel table-scroll"><table><thead><tr><th>工作流 / 操作</th><th>状态</th><th>付款人</th><th>最近更新</th></tr></thead><tbody>'
    +items.map(item=>'<tr><td><a href="#projects/'+encode(item.id)+'">'+esc(item.workflow_id||'历史运行')+' / '+esc(item.operation)
      +'</a><br><small>'+esc(displayId(item.id))+(item.workflow_revision?' · 目录修订 '+esc(item.workflow_revision):'')
      +'</small></td><td>'+badge(ui,item.status)+'</td><td>'+esc(item.payer||'—')+'</td><td>'
      +esc(formatTime(item.last_event||item.created))+'</td></tr>').join('')+'</tbody></table></div>';
}

function inboxRows(ui,items){
  return items.map(item=>{
    const target='#projects/'+encode(item.project_id)+'?focus='+encode(item.kind+':'+item.reference);
    return '<a class="inbox-row" href="'+target+'"><span>'+ui.esc(kinds[item.kind])+'</span><strong>'
      +ui.esc((item.workflow_id||'历史运行')+' / '+item.operation)+'</strong><small>'
      +ui.esc(formatTime(item.created))+'</small><span aria-hidden="true">→</span></a>';
  }).join('');
}

export async function showRuns(ui){
  const params=new URLSearchParams(ui.params);
  const result=await ui.api('workbench/runs?'+params.toString());
  if(!ui.current())return;
  const states=['queued','running','waiting','awaiting_approval','completed','partial','failed','interrupted','rejected','cancelled'];
  ui.content.innerHTML=ui.heading('运行记录','当前 Workspace · 搜索历史运行并查看交付。')
    +'<form id="run-filter" class="panel filter-bar"><label>关键词<input type="search" name="q" maxlength="100" placeholder="工作流、操作或运行 ID" value="'
    +ui.esc(params.get('q'))+'"></label><label>状态<select name="status"><option value="">全部状态</option>'
    +states.map(status=>'<option value="'+status+'"'+(status===params.get('status')?' selected':'')+'>'+ui.esc(ui.label[status])+'</option>').join('')
    +'</select></label><button type="submit">筛选</button></form>'+runTable(ui,result.items)+pageLink('projects',params,result.next_cursor);
  ui.content.querySelector('#run-filter').onsubmit=event=>{
    event.preventDefault();
    const query=new URLSearchParams([...new FormData(event.target)].filter(([,value])=>value.trim()));
    location.hash='projects'+(query.size?'?'+query:'');
  };
}

export async function showInbox(ui){
  const params=new URLSearchParams(ui.params);
  const result=await ui.api('workbench/inbox?'+params.toString());
  if(!ui.current())return;
  const selected=params.get('kind');
  ui.content.innerHTML=ui.heading('待处理','当前 Workspace · 只显示需要核实或作出人工决定的事项。')
    +'<div class="section-tabs inbox-filters"><a href="#attention"'+(!selected?' aria-current="page"':'')+'>全部</a>'
    +Object.entries(kinds).map(([kind,label])=>'<a href="#attention?kind='+kind+'"'+(selected===kind?' aria-current="page"':'')+'>'+ui.esc(label)+'</a>').join('')
    +'</div>'+(result.items.length?'<div class="panel inbox-list">'+inboxRows(ui,result.items)+'</div>':ui.empty('此分类暂无待办。'))
    +pageLink('attention',params,result.next_cursor);
}

function fileLinks(ui,id){
  const path='/api/v1/artifacts/'+encode(id), query='?workspace='+encode(ui.workspace());
  return '<a class="action-link" href="'+path+'/preview'+query+'">预览</a><a class="action-link" href="'+path+'/download'+query+'">下载</a>';
}
function receiptLink(ui,id){
  return '/api/v1/requests/'+encode(id)+'?workspace='+encode(ui.workspace());
}
function marked(kind,id){return ' data-focus="'+kind+':'+encode(id)+'"';}

function runSections(ui,run,grants){
  const {esc}=ui;
  const actions=new Set(run.actions),allow=action=>actions.has(action);
  const active=['queued','running','waiting','awaiting_approval'].includes(run.status);
  const names=new Map(run.tasks.map(task=>[task.id,task.title||task.node_key||task.name]));
  function forTask(items,key,id){
    return items.filter(item=>(item[key]||run.root_task_id)===id);
  }
  function questions(items){
    return items.map(q=>'<section class="task"'+marked('question',q.id)+'><h3>待答复</h3><p>'+esc(q.content)+'</p>'
      +(allow('ask')&&run.status!=='cancelled'?'<form data-question="'+esc(q.id)+'"><label>回复<textarea name="answer" rows="3" maxlength="8000" required></textarea></label><button>提交答复</button></form>':'')
      +'</section>').join('');
  }
  function approvals(items){
    return items.map(a=>'<section class="task"'+marked('approval',a.id)+'><h3>外部写操作审批</h3><p>'
      +esc(a.logical_tool+' · '+a.endpoint_ref)+' '+badge(ui,a.status,a.status==='pending'?'待审批':undefined)+'</p><p class="muted">版本 '
      +esc(a.implementation_version)+'</p>'+(run.can_decide_tool?'<button data-approval="'+esc(a.id)+'">查看目标与参数</button>':'')+'</section>').join('');
  }
  function evidence(items){
    return items.map(item=>'<div class="task">'+badge(ui,item.status)+' <button data-evidence="'+esc(item.id)+'">查看证据</button></div>').join('');
  }
  function calls(items){
    return items.map(r=>'<p'+marked(r.status==='unknown'?'receipt':'accounting',r.id)+'>'+esc(r.kind||'调用')
      +' · '+esc(ui.label[r.status]||r.status)+' · '+esc(globalThis.OrchpactAttention.accountingLabel(r))
      +' · <a href="'+receiptLink(ui,r.id)+'">查看回执</a>'
      +(ui.me.admin&&run.payer_user_id&&r.status!=='started'&&r.accounting?.state==='held'
        ?' · <a href="#account?credits='+encode(run.payer_user_id)+'">核定积分</a>':'')+'</p>').join('');
  }
  function history(task){
    const requests=forTask(run.requests,'task_id',task.id);
    const tools=forTask(run.tool_invocations,'task_id',task.id);
    const steps=forTask(run.agent_steps,'task_id',task.id);
    if(!requests.length&&!tools.length&&!steps.length)return '';
    return '<details><summary>调用记录与费用</summary>'+calls(requests)
      +tools.map(call=>'<p>'+esc(call.logical_tool+' · '+call.status)
        +(call.status==='unknown'&&call.request_id?' · <a href="'+receiptLink(ui,call.request_id)+'">核对原回执</a>':'')+'</p>').join('')
      +(steps.length?'<h4>CrewAI 岗位行动</h4><ol>'+steps.map(step=>'<li>'+esc([step.member_key,step.kind,step.name,step.status].filter(Boolean).join(' · '))+'</li>').join('')+'</ol>':'')+'</details>';
  }
  const plan='<section class="flow-run-panel" data-run-id="'+esc(run.id)+'" aria-label="运行画布"><div class="flow-split">'
    +'<div data-run-flow></div><aside class="flow-inspector" data-run-inspector aria-label="运行节点详情">'
    +run.tasks.map(task=>{
      const root=task.id===run.root_task_id;
      const prerequisites=(run.dependencies[task.id]||[]).map(key=>names.get(key)||key);
      const queuedReview=task.artifact_id&&allow('review')&&task.review==='pending'&&task.required!==false&&run.status!=='cancelled';
      return '<article class="task" data-graph-task="'+esc(task.id)+'" hidden'+marked('review',task.id)+'><header><span>'
        +esc(root?'运行结果':task.node_key||task.name)+'</span><div class="row"><h2>'+esc(root?'最终交付':task.title||task.name)
        +'</h2>'+badge(ui,task.status)+'</div></header>'
        +(!root||!run.execution_plan?'<p>'+esc(task.description)+'</p>':'')
        +(prerequisites.length?'<p class="muted">依赖 '+esc(prerequisites.join('、'))+'</p>':'')
        +(task.error?'<p class="error">'+esc(task.error)+'</p>':'')
        +questions(forTask(run.questions,'from_task',task.id))+approvals(forTask(run.tool_approvals,'task_id',task.id))
        +(task.artifact_id?'<p>交付物 '+esc(displayId(task.artifact_id))+' '+(root && task.review==='accepted'
          ? '<span class="delivery-accepted"><b aria-hidden="true">✓</b> 交付已验收</span>' : badge(ui,task.review))+'</p>':'')
        +'<div class="actions">'+(task.artifact_id?fileLinks(ui,task.artifact_id):'')
        +(queuedReview?'<button data-review="'+esc(task.id)+'">验收此版本</button>':'')
        +(allow('retry')&&run.retryable_tasks.includes(task.id)?'<button data-retry="'+esc(task.id)+'">按原付款人重试</button>':'')
        +'<button data-attempts="'+esc(task.id)+'">查看尝试</button></div><div data-task-detail></div>'
        +evidence(forTask(run.verification_evidence,'task_id',task.id))+history(task)+'</article>';
    }).join('')+'<div data-run-node-controls></div></aside></div></section>';
  const blurb='<div class="row"'+marked('execution',run.id)+'><h2>运行信息</h2>'+badge(ui,run.status)+'</div>'
    +'<dl class="run-facts"><div><dt>开始时间</dt><dd>'+esc(formatTime(run.created))+'</dd></div><div><dt>原付款人</dt><dd>'
    +esc(run.payer||run.payer_user_id||'—')+'</dd></div><div><dt>已知用量</dt><dd>'+esc(run.usage.points)
    +' 积分'+(run.usage.complete?'':' · 部分用量待确认')+'</dd></div><div><dt>计划修订</dt><dd>'+run.revision
    +'</dd></div></dl><p class="muted">上限 · 请求 '+run.limits.request_limit+' / 搜索 '+run.limits.search_limit+' / 工具 '+run.limits.tool_limit
    +'</p><div class="actions">'+(active&&allow('cancel')?'<button id="cancel-run">取消后续执行</button>':'')
    +(allow('delegate')?'<button id="grant-run">授权</button>':'')+'</div>';
  const authority='<details class="workbench-details"><summary>项目授权</summary>'
    +(grants.length?grants.map(grant=>'<div class="task"><strong>'+esc(grant.subject)+'</strong><br><small>'
      +esc(grant.actions.join('、'))+' · '+esc(grant.revoked?'已撤销':formatTime(grant.expires))+'</small>'
      +(allow('delegate')&&!grant.revoked?'<p><button data-revoke="'+esc(grant.id)+'">撤销授权</button></p>':'')+'</div>').join(''):'<p class="muted">暂无授权记录。</p>')+'</details>';
  const events='<details class="workbench-details"><summary>运行时间线</summary>'
    +'<ol data-events></ol><button id="load-events">载入事件</button></details>';
  return plan+'<details class="panel workbench-details"><summary>运行详情与管理</summary>'+blurb+events+authority+'</details>';
}

export async function showRun(ui){
  const {api,key,current,content,heading,esc,formDialog,jsonDialog,tell,render}=ui;
  const path='runs/'+encode(key);
  const [run,grants]=await Promise.all([api('workbench/'+path),api(path+'/mandates')]);
  if(!current())return;
  const following=['queued','running','waiting','awaiting_approval'].includes(run.status);
  if(following){
    // Keep human input and wire gestures intact across asynchronous task updates.
    function signature(value){
      return JSON.stringify([value.revision,value.status,value.tasks.map(task=>[task.id,task.status,task.artifact_id,task.review]),
        value.questions.map(item=>item.id),value.tool_approvals.map(item=>[item.id,item.status]),
        value.requests.map(item=>[item.id,item.status,item.accounting?.state]),value.verification_evidence.length]);
    }
    const displayed=signature(run);
    let timer;
    async function poll(){
      if(!current())return;
      try{
        const next=await api('workbench/'+path);
        if(!current())return;
        const busy=document.querySelector('#editor,[role="dialog"]')||content.querySelector('.flow-viewport[data-gesture],.is-source,.is-panning,[data-multiselect][aria-pressed=true]')
          ||content.querySelector('form:focus-within,form textarea[data-dirty],.workbench-details[open],'
            +'[data-graph-task]:not([hidden]) details[open],[data-graph-task]:not([hidden]) [data-task-detail]:not(:empty),'
            +'[data-graph-task]:not([hidden]) [data-evidence][disabled]');
        if(busy||signature(next)===displayed){
          timer=setTimeout(poll,2500);
          return;
        }
        const inspected=content.querySelector('[data-run-inspector]');
        const scrollTop=inspected.scrollTop, pageY=window.scrollY;
        const focusedCard=document.activeElement?.closest('[data-node]')?.dataset.node;
        await render();
        if(content.querySelector('[data-run-id]')?.dataset.runId!==run.id)return;
        content.querySelector('[data-run-inspector]').scrollTop=scrollTop;
        if(focusedCard&&document.activeElement===document.body)
          content.querySelector('[data-node="'+CSS.escape(focusedCard)+'"] .flow-node-body')?.focus({preventScroll:true});
        window.scrollTo({top:pageY,behavior:'instant'});
      }catch(error){if(current())tell(error.message,true);}
    }
    timer=setTimeout(poll,2500);
    ui.signal.addEventListener('abort',()=>clearTimeout(timer),{once:true});
  }
  const title=run.workflow_name||run.operation;
  const subtitle=[run.workflow?.id||'历史运行',run.workflow&&'目录修订 '+run.workflow.revision,
    run.workflow?.installation_revision&&'安装 #'+run.workflow.installation_revision,'Run '+run.id].filter(Boolean).join(' · ');
  content.innerHTML=heading(title,subtitle,'<a class="action-link" href="#projects">返回运行记录</a>')
    +runSections(ui,run,grants);
  const action=fn=>async event=>{
    try{await fn(event);}
    catch(error){if(current())tell(error.message,true);}
  };
  const refresh=async message=>{if(current()){tell(message);await render();}};
  mountRunCanvas(ui,run,refresh);
  function bind(selector,handler){
    for(const node of content.querySelectorAll(selector))node.onclick=action(event=>handler(node,event));
  }
  const command=(action,payload,idempotencyKey)=>api(path+'/commands',{method:'POST',value:{key:idempotencyKey,action,payload}});
  bind('#cancel-run',async()=>{
    if(!confirm('取消此运行的后续执行？已经发出的请求仍可能返回结果或产生费用。'))return;
    await api(path+'/cancel',{method:'POST',value:{}});
    await refresh('已取消后续执行');
  });
  bind('#grant-run',()=>jsonDialog('项目授权',{subject:'user:',actions:['review'],capabilities:[],expires:Math.floor(Date.now()/1000)+3600,budget_points:null},
    '授权持续到设定期限，并受原授权范围限制。',value=>api(path+'/mandates',{method:'POST',value})));
  bind('[data-revoke]',async node=>{
    if(!confirm('撤销此授权及其转授？'))return;
    await api(path+'/mandates/'+encode(node.dataset.revoke),{method:'DELETE'});
    await refresh('已撤销');
  });
  bind('[data-attempts]',async node=>{
    const attempts=await api('work-items/'+encode(node.dataset.attempts)+'/attempts');
    if(!current())return;
    const detail=node.closest('.task').querySelector('[data-task-detail]');
    detail.innerHTML=attempts.length?'<ol>'+attempts.map(item=>'<li>'+esc(ui.label[item.status]||item.status)+' · '
      +esc(item.model_id)+' · '+esc(formatTime(item.started))+(item.error?' · '+esc(item.error):'')+'</li>').join('')+'</ol>':'<p>尚无执行尝试。</p>';
  });
  bind('[data-evidence]',async node=>{
    const result=await api('verification-evidence/'+encode(node.dataset.evidence));
    if(!current())return;
    node.closest('.task').insertAdjacentHTML('afterend','<pre>'+esc(JSON.stringify(result,null,2))+'</pre>');
    node.disabled=true;
  });
  let eventCursor=0;
  bind('#load-events',async node=>{
    node.disabled=true;
    try {
      const events=await api(path+'/events?limit=25&cursor='+eventCursor);
      if(!current())return;
      content.querySelector('[data-events]').insertAdjacentHTML('beforeend',events.items.map(event=>
        '<li><time>'+esc(formatTime(event.created))+'</time> · '+esc(event.kind)+'</li>').join(''));
      eventCursor=events.cursor;
      node.textContent=events.items.length<25?'已加载全部事件':'加载更多事件';
      node.disabled=events.items.length<25;
    } catch(error) {
      node.disabled=false;
      throw error;
    }
  });
  bind('[data-approval]',async node=>{
    const approvalPath=path+'/tool-approvals/'+encode(node.dataset.approval);
    const item=await api(approvalPath);
    if(!current())return;
    if(item.status!=='pending'){
      node.closest('.task').insertAdjacentHTML('beforeend','<pre>'+esc(JSON.stringify(item.arguments,null,2))+'</pre>');
      node.disabled=true;
      return;
    }
    formDialog({title:'审批外部写操作',caption:'提交决定',
      body:'<p>'+esc(item.endpoint_ref+' · '+item.implementation_version)+'</p><pre>'
        +esc(JSON.stringify(item.arguments,null,2))+'</pre><label>决定<select name="decision" required><option value="">选择决定</option>'
        +'<option value="approve">批准并排队</option><option value="reject">拒绝此次调用</option></select></label>',
      async onSubmit(form,modal){
        await api(approvalPath+'/decision',{method:'POST',value:{decision:form.elements.decision.value,approval_digest:item.approval_digest}});
        modal.close();
        await refresh('审批决定已记录');
      },
    });
  });
  bind('[data-review]',async node=>{
    const task=run.tasks.find(item=>item.id===node.dataset.review);
    const artifact=await api('artifacts/'+encode(task.artifact_id));
    if(!current())return;
    formDialog({title:'验收交付物',caption:'提交验收',
      body:'<p>'+esc(artifact.name)+' · <code>'+esc(artifact.sha256)+'</code></p><div class="actions">'
        +fileLinks(ui,artifact.id)+'</div><label>决定<select name="verdict"><option value="accept">接受此版本</option>'
        +'<option value="rework">要求返工</option><option value="waive">记录缺口</option></select></label>'
        +'<label>验收依据<textarea name="feedback" rows="4" maxlength="4000" required></textarea></label>',
      async onSubmit(form,modal){
        const payload={artifact_id:artifact.id,sha256:artifact.sha256,verdict:form.elements.verdict.value,feedback:form.elements.feedback.value.trim()};
        await command('review',payload,modal.keyFor(payload));
        modal.close();
        await refresh('验收决定已记录');
      },
    });
  });
  bind('[data-retry]',async node=>{
    if(!confirm('按原付款人重试此工作项？'))return;
    node.disabled=true;
    const id=node.dataset.key||crypto.randomUUID();
    node.dataset.key=id;
    try{await api('work-items/'+encode(node.dataset.retry)+'/retry',{method:'POST',value:{},headers:{'Idempotency-Key':id}});await refresh('已请求重试');}
    finally{node.disabled=false;}
  });
  for(const text of content.querySelectorAll('[data-question] textarea')){
    text.oninput=()=>text.toggleAttribute('data-dirty',Boolean(text.value));
  }
  for(const form of content.querySelectorAll('[data-question]'))form.onsubmit=action(async event=>{
    event.preventDefault();
    const button=form.querySelector('button');
    if(button.disabled)return;
    button.disabled=true;
    try{
      await api('questions/'+encode(form.dataset.question)+'/answers',{method:'POST',value:{content:form.elements.answer.value}});
      await refresh('已提交答复');
    } finally{button.disabled=false;}
  });
  const focus=ui.params.get('focus');
  const target=[...content.querySelectorAll('[data-focus]')].find(node=>node.dataset.focus===focus);
  const token=focus?JSON.stringify([ui.workspace(),key,focus]):null;
  const shouldFocus=target&&token!==lastFocus.get(content);
  lastFocus.set(content,token);
  if(shouldFocus){
    for(let ancestor=target.parentElement;ancestor;ancestor=ancestor.parentElement){
      if(ancestor.tagName==='DETAILS')ancestor.open=true;
    }
    target.classList.add('attention-target');
    target.scrollIntoView({block:'center'});
  }
}
