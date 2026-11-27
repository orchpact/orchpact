import {mountFlow,introducesCycle} from './flow-canvas.js';

const encode=encodeURIComponent;
const editable=task=>['queued','waiting'].includes(task.status)&&!task.turns&&task.required;
const active=run=>['queued','running','waiting','awaiting_approval'].includes(run.status);
const names=task=>task.title||task.node_key||task.name;
let lastView;

function focusedTask(focus,run){
  const [type,id]=(focus||'').split(':');
  if(type==='review')return id;
  if(type==='question')return run.questions.find(item=>item.id===id)?.from_task||run.root_task_id;
  if(type==='approval')return run.tool_approvals.find(item=>item.id===id)?.task_id;
  if(type==='accounting'||type==='receipt')return run.requests.find(item=>item.id===id)?.task_id;
  if(type==='execution')return run.root_task_id;
}

export function mountRunCanvas(ui,run,refresh){
  const {api,esc,content,formDialog,current}=ui;
  const host=content.querySelector('[data-run-flow]');
  const panel=content.querySelector('[data-run-inspector]');
  const root=run.tasks.find(task=>task.id===run.root_task_id);
  const tasks=run.tasks.filter(task=>task.id!==root?.id);
  const byId=new Map(run.tasks.map(task=>[task.id,task]));
  const attention=run.questions[0]?.from_task||run.tool_approvals.find(item=>item.status==='pending')?.task_id
    ||tasks.find(task=>task.review==='pending'&&task.requires_independent_review&&task.artifact_id)?.id;
  const key=ui.workspace()+':'+run.id;
  const state=lastView?.key===key?lastView:{key,view:{},positions:{}};
  lastView=state;
  const eligible=active(run)&&run.actions.includes('amend');
  const canAdd=Boolean(eligible&&run.actions.includes('recruit')&&run.actions.includes('delegate')&&run.execution_plan
    &&root?.kind==='group'&&root.required&&['queued','waiting'].includes(root.status));
  const canFork=Boolean(ui.me.admin&&run.workflow?.id&&run.workflow?.revision&&run.execution_plan);
  const liveSource=task=>task.id!==root?.id&&task.required&&Boolean(task.node_key);
  const liveTarget=task=>eligible&&task.id!==root?.id&&editable(task);
  const forkable=task=>canFork&&(task.id===root?.id||Boolean(task.node_key));
  const nodes=run.tasks.map(task=>{
    const review=task.review==='pending'&&task.artifact_id&&task.requires_independent_review;
    const question=run.questions.some(item=>(item.from_task||run.root_task_id)===task.id);
    const add=(canAdd&&liveSource(task))||forkable(task);
    const isRoot=task.id===root?.id;
    const approved=Boolean(task.artifact_id&&task.review==='accepted'&&task.status==='completed');
    const state=question||review?'review':approved?'accepted':task.review==='waived'?'waived':task.status;
    const meta=question?'待答复':review?'待验收':approved?(isRoot?'交付已验收':'环节已验收'):
      state==='completed'?'运行完成':ui.label[state]||state;
    return {id:task.id,title:isRoot?'最终交付':(task.title||task.description||names(task)),
      type:isRoot?'result':task.kind,kind:isRoot?'运行结果':task.kind==='group'?'汇总':names(task),
      meta,state,add,input:liveTarget(task),output:add||(eligible&&liveSource(task))};
  });
  const edges=run.tasks.flatMap(task=>[
    ...(run.dependencies[task.id]||[]).map(from=>({from,to:task.id})),
    ...(task.parent_id?[{from:task.id,to:task.parent_id,group:true}]:[]),
  ]);
  const controls=panel.querySelector('[data-run-node-controls]');
  const path='runs/'+encode(run.id)+'/commands';
  function forkRoute(task,branch=false,selection=[]){
    const query=new URLSearchParams({view:'design',revision:run.workflow.revision,operation:run.operation,
      run:run.id,plan_revision:run.revision,fork:'1'});
    if(task.node_key)query.set('node',task.node_key);
    for(const key of selection)query.append('select',key);
    if(branch)query.set('branch','1');
    return '#workflows/'+encode(run.workflow.id)+'?'+query.toString();
  }
  async function amend(value,modal,position){
    await api(path,{method:'POST',value:{action:'amend',revision:run.revision,key:modal.keyFor(value),payload:value}});
    state.added=value.add_nodes?.[0]?.key;
    state.addedPosition=position;
    modal.close();
    await refresh('计划变更已记录');
  }
  function editBranch(task,newSource){
    const before=run.dependencies[task.id]||[];
    const sources=tasks.filter(item=>item.id!==task.id&&(before.includes(item.id)
      ||(item.required&&!introducesCycle(edges,item.id,task.id))));
    formDialog({title:'调整依赖 · '+names(task),caption:'保存计划变更',
      body:'<p class="muted">计划修订 '+esc(run.revision)+' · 这个环节会等待所有选中的上游通过验收。</p>'
        +'<fieldset class="flow-dependencies"><legend>上游节点</legend>'+sources.map(source=>
          '<label><input type="checkbox" name="upstream" value="'+esc(source.id)+'"'
          +(before.includes(source.id)||source.id===newSource?' checked':'')+'> '+esc(names(source))+'</label>').join('')+'</fieldset>'
        +'<label>变更原因<textarea name="reason" required maxlength="2000" rows="3"></textarea></label>',
      onSubmit(form,modal){
        const deps=new FormData(form).getAll('upstream');
        if(deps.length===before.length&&deps.every(id=>before.includes(id)))throw Error('请选择需要变更的依赖');
        return amend({reason:form.elements.reason.value,changes:[{task_id:task.id,depends_on:deps}]},modal);
      },
    });
  }
  function removeBranch(task){
    formDialog({title:'取消待执行节点 · '+names(task),caption:'提交取消',
      body:'<p class="muted">如仍有下游引用此节点，请先调整依赖。运行记录会保留这次计划变更。</p>'
        +'<label>变更原因<textarea name="reason" required maxlength="2000" rows="3"></textarea></label>',
      onSubmit(form,modal){return amend({reason:form.elements.reason.value,changes:[{task_id:task.id,cancel:true}]},modal);},
    });
  }
  async function addBranch(source,position){
    const models=(await api('models')).filter(model=>['text','json'].every(capability=>model.capabilities.includes(capability)));
    if(!current())return;
    if(!models.length){ui.tell('请先在平台设置中配置文本模型',true);return;}
    const used=Object.values(run.workflow?.bindings?.models||{});
    const defaultModel=models.find(model=>used.includes(model.id))?.id||models[0].id;
    const targets=tasks.filter(task=>liveTarget(task)&&task.id!==source.id&&!introducesCycle(edges,source.id,task.id));
    formDialog({title:'从 '+names(source)+' 添加分支',caption:'加入运行计划',
      body:'<p class="muted">新节点等上游通过后执行，使用本次运行的付款人和预算。</p>'
        +'<label>环节名称<input name="title" maxlength="120" placeholder="例如，核对配送地址"></label>'
        +'<label>步骤指令<textarea name="instruction" required maxlength="12000" rows="4"></textarea></label>'
        +'<label>执行岗位<input name="role" value="分支执行者" required maxlength="100"></label>'
        +'<label>文本模型<select name="model">'+models.map(model=>'<option value="'+esc(model.id)+'"'
          +(model.id===defaultModel?' selected':'')+'>'+esc(model.label)+'</option>').join('')+'</select></label>'
        +'<label>接入下游<select name="target"><option value="">并行分支，完成后纳入运行汇总</option>'
        +targets.map(task=>'<option value="'+esc(task.id)+'">'+esc(names(task))+'</option>').join('')+'</select></label>'
        +'<label>变更原因<textarea name="reason" required maxlength="2000" rows="3"></textarea></label>',
      mount(form){form.dataset.branchKey='branch-'+crypto.randomUUID().slice(0,12);},
      onSubmit(form,modal){
        const {instruction,role,model,target,reason}=form.elements;
        const title=form.elements.namedItem('title').value.trim();
        const name=form.dataset.branchKey,filename=name+'.json';
        const node={key:name,kind:'agent',...(title?{title}:{}),description:instruction.value.trim(),expected:filename,
          staff:{name:role.value.trim(),role:'执行当前分支',model_id:model.value,reason:'用户追加分支',capabilities:['text','json']},
          outputs:{[filename]:{type:'object'}},depends_on:[source.node_key]};
        const changes=target.value?[{task_id:target.value,add_dependencies:[name]}]:[];
        return amend({reason:reason.value,add_nodes:[node],changes},modal,position);
      },
    });
  }
  function startBranch(id,position){
    const task=byId.get(id);
    if(canAdd&&liveSource(task)){
      addBranch(task,position).catch(error=>{if(current())ui.tell(error.message,true);});
    }else if(forkable(task)){
      location.hash=forkRoute(task,true);
    }
  }
  function connect(from,to){
    if(!eligible||!liveSource(byId.get(from))||!liveTarget(byId.get(to)))throw Error('运行中只能连到尚未开始的环节');
    if(introducesCycle(edges,from,to))throw Error('这条连线会形成循环');
    if(run.dependencies[to]?.includes(from))throw Error('这两个环节已经连接');
    editBranch(byId.get(to),from);
    return '请确认这次依赖变更';
  }
  const keyFor=task=>task.node_key||(task.id===root?.id?run.execution_plan?.result_node:null);
  function trackSelection(ids){
    state.selection=ids;
  }
  function inspectMany(ids){
    if(ids.length===1){inspect(byId.get(ids[0]));return;}
    state.selected=ids.at(-1);
    trackSelection(ids);
    for(const article of panel.querySelectorAll('[data-graph-task]'))article.hidden=true;
    const items=ids.map(id=>byId.get(id));
    controls.innerHTML=items.length?'<h2>已选 '+items.length+' 个节点</h2><ul>'+items.map(task=>
      '<li>'+esc(task.id===root?.id?'最终交付 · '+keyFor(task):names(task))+'</li>').join('')+'</ul>'
      +(items.includes(root)?'<p class="muted">最终交付对应工作流声明的结果环节，同一环节只计入一次。</p>':'')
      +(items.some(item=>!keyFor(item))?'<p class="muted">此选区包含没有可移植定义的临时工作项。</p>':'')
      :'<p class="muted">选择画布节点查看运行详情，或框选多个环节进行封装。</p>';
  }
  function inspect(task){
    if(!task)return;
    state.selected=task.id;
    trackSelection([task.id]);
    for(const item of panel.querySelectorAll('[data-graph-task]'))item.hidden=item.dataset.graphTask!==task.id;
    const buttons=[];
    if(canAdd&&liveSource(task))buttons.push('<button data-add-branch>从此节点添加分支</button>');
    if(liveTarget(task))buttons.push('<button data-edit-branch>调整此节点的依赖</button><button data-remove-branch>取消此待执行节点</button>');
    if(forkable(task))buttons.push('<a href="'+esc(forkRoute(task))+'">从此节点另存工作流</a>');
    controls.innerHTML='<div class="flow-buttons">'+buttons.join('')+'</div>'
      +(canFork&&task===root?'<p class="muted">封装最终交付将使用结果环节 '+esc(keyFor(task))+'。</p>':'');
    const edit=controls.querySelector('[data-edit-branch]');
    if(edit)edit.onclick=()=>editBranch(task);
    const remove=controls.querySelector('[data-remove-branch]');
    if(remove)remove.onclick=()=>removeBranch(task);
    const add=controls.querySelector('[data-add-branch]');
    if(add)add.onclick=()=>startBranch(task.id);
  }
  const graph=mountFlow(host,{signal:ui.signal,state:state.view,select:id=>inspect(byId.get(id)),
    ...(eligible?{connect}:{}),...(canAdd||canFork?{add:startBranch}:{}),
    ...(canFork?{selectMany:inspectMany,clearOnMultiple:true,
      canPackage:ids=>Boolean(ids.length&&ids.every(id=>keyFor(byId.get(id)))),
      packageSelection(ids){
        const keys=[...new Set(ids.map(id=>keyFor(byId.get(id))))];
        const first=tasks.find(task=>task.node_key===keys[0]);
        if(first)location.hash=forkRoute(first,false,keys);
      }}:{})});
  const focus=ui.params.get('focus');
  const added=run.tasks.find(task=>task.node_key&&task.node_key===state.added);
  if(added&&state.addedPosition)state.positions[added.id]=state.addedPosition;
  const focusId=focus&&state.focus!==focus?focusedTask(focus,run):null;
  const selected=added||byId.get(focusId)||byId.get(state.selected)||byId.get(attention)||root;
  state.focus=focus;
  const retained=!added&&!focusId&&canFork&&state.selection?.filter(id=>byId.has(id));
  const ids=retained||[selected?.id].filter(Boolean);
  graph.setGraph(nodes,edges,{selected:ids.at(-1),selectedIds:ids,positions:state.positions,fit:Boolean(added&&!state.addedPosition)});
  delete state.added;
  delete state.addedPosition;
  inspectMany(ids);
}
