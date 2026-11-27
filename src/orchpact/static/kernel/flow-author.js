import {mountFlow,introducesCycle,freeFlowPosition} from './flow-canvas.js';
import {extractWorkflow,insertWorkflow} from './flow-package.js';

const idPattern='[a-zA-Z][a-zA-Z0-9_\\-]{0,63}';
const objectSchema={type:'object'};
const kindOf=node=>node.kind||(node.staff||node.participants?.length?'agent':'group');
const kinds={agent:'Agent',tool:'工具',group:'汇总'};
const display=(node,plan)=>node.key===plan.result_node?'最终交付':kinds[kindOf(node)];
const links=nodes=>nodes.flatMap(node=>[
  ...(node.depends_on||[]).map(from=>({from,to:node.key})),
  ...(node.parent?[{from:node.key,to:node.parent,group:true}]:[]),
]);

function readStore(storage,key,fallback){
  try{return JSON.parse(storage.getItem(key))??fallback;}catch{return fallback;}
}
function writeStore(storage,key,value){
  try{storage.setItem(key,JSON.stringify(value));return true;}catch{return false;}
}
function object(text){
  const value=JSON.parse(text);
  if(!value||typeof value!=='object'||Array.isArray(value))throw Error('这项内容需要一个 JSON 对象');
  return value;
}
function draftNode(key,unit,model,expected=key+'.json',schema=objectSchema){
  return {key,kind:'agent',unit,description:'根据输入生成约定交付。',expected,
    outputs:{[expected]:schema},staff:{name:'交付执行者',role:'按步骤指令执行',reason:'执行声明的工作流步骤',
      capabilities:['text','json'],...(model?{model}:{})}};
}
function blankDefinition(){
  const output={type:'object',additionalProperties:false,properties:{summary:{type:'string'}},required:['summary']};
  return {format:'orchpact-workflow',id:'',name:'',version:'1.0.0',
    resources:{models:{reasoning:{capabilities:['text','json']}}},
    units:[{key:'entry',name:'交付团队',purpose:'按约定交付成果',model:'reasoning'}],
    operations:{generate:{unit:'entry',instruction:'按步骤说明处理输入并交付结果。',
      input_schema:{type:'object',properties:{},additionalProperties:false},output_schema:output,output_file:'result.json',
      execution_plan:{result_node:'',nodes:[]}}}};
}
function nextNodeKey(nodes){
  const keys=new Set(nodes.map(node=>node.key));
  let ordinal=1;
  while(keys.has('step-'+ordinal))ordinal++;
  return 'step-'+ordinal;
}
function agentStage(value,op,key,unit,first){
  const model=Object.entries(value.resources?.models||{}).find(([,slot])=>
    ['text','json'].every(capability=>slot.capabilities?.includes(capability)))?.[0];
  return first?draftNode(key,unit,model,op.output_file||'result.json',op.output_schema):draftNode(key,unit,model);
}
function nameFork(value){
  value.id=value.id.slice(0,62)+'-branch-'+crypto.randomUUID().slice(0,8);
  value.name=value.name.slice(0,115)+' · 分支';
}
function displayNodes(plan){
  return plan.nodes.map(node=>({id:node.key,type:node.key===plan.result_node?'result':kindOf(node),kind:display(node,plan),title:node.title||node.description,meta:node.key,
    state:node.key===plan.result_node?'result':kindOf(node)}));
}
function storagePrefix(ui){
  return 'orchpact.canvas:'+ui.me.user_id+':'+ui.workspace()+':';
}

export function canvasDrafts(ui){
  const base=storagePrefix(ui)+'draft:new',items=[];
  try{
    for(let index=0;index<sessionStorage.length;index++){
      const key=sessionStorage.key(index);
      if(key!==base&&!key?.startsWith(base+':'))continue;
      const token=key===base?'':key.slice(base.length+1);
      if(token&&!/^[0-9a-f-]{36}$/i.test(token))continue;
      const entry=readStore(sessionStorage,key,null);
      if(entry?.value?.format!=='orchpact-workflow')continue;
      items.push({href:token?'#workflows?view=design&draft='+encodeURIComponent(token):'#canvas',
        name:entry.value.name||'未命名工作流'});
    }
  }catch{return [];}
  return items;
}

export function saveCanvasSeed(ui,definition=blankDefinition(),initialState={}){
  const token=crypto.randomUUID();
  if(!writeStore(sessionStorage,storagePrefix(ui)+'draft:new:'+token,{...initialState,value:definition})){
    throw Error('当前标签页无法保存草稿，请允许浏览器使用会话存储后重试。');
  }
  return 'workflows?view=design&draft='+token;
}

export function forkCanvasSeed(ui,definition,nodeKey,operationKey){
  const value=structuredClone(definition);
  const op=Object.hasOwn(value.operations,operationKey)?value.operations[operationKey]:null;
  const nodes=op?.execution_plan?.nodes,source=nodes?.find(node=>node.key===nodeKey);
  if(!source)throw Error('此修订找不到所选环节');
  if(nodes.length>=40)throw Error('每个操作最多 40 个节点，请先将部分环节封装为工作流。');
  const key=nextNodeKey(nodes);
  const stage=agentStage(value,op,key,source.unit||op.unit||value.units?.[0]?.key,false);
  stage.depends_on=[nodeKey];
  nodes.push(stage);
  nameFork(value);
  return saveCanvasSeed(ui,value,{operation:operationKey,selected:key,selection:[key]})
    +'&'+new URLSearchParams({operation:operationKey,node:nodeKey,branch:'1'});
}

export function previewFlow(host,ui,definition,{branch}={}){
  const entries=Object.entries(definition.operations);
  if(!entries.length){host.innerHTML=ui.empty('没有可查看的操作。');return;}
  const {esc}=ui;
  host.innerHTML='<div class="flow-top"><label>操作 <select>'+entries.map(([key])=>
    '<option>'+esc(key)+'</option>').join('')+'</select></label><output aria-live="polite"></output></div><div data-preview-flow></div>';
  const header=host.querySelector('.flow-top'),choice=header.querySelector('select');
  const layouts=readStore(localStorage,storagePrefix(ui)+'layout:'+(definition.id||''),{});
  let plan;
  const graph=mountFlow(host.querySelector('[data-preview-flow]'),{signal:ui.signal,
    add:branch?async key=>{
      try{await branch(key,choice.value);}
      catch(error){if(ui.current())ui.tell(error.message,true);}
    }:undefined,
    select(key){
    const node=plan.nodes.find(item=>item.key===key);
    header.querySelector('output').textContent=node.description+' · '+node.expected;
  }});
  function redraw(){
    plan=definition.operations[choice.value].execution_plan;
    header.querySelector('output').textContent=plan?'':'此操作在运行时规划环节，可在运行画布查看。';
    host.querySelector('[data-preview-flow]').hidden=!plan;
    graph.setGraph(plan?displayNodes(plan):[],plan?links(plan.nodes):[],{fit:true,positions:Object.hasOwn(layouts,choice.value)?layouts[choice.value]:{}});
  }
  choice.onchange=redraw;
  redraw();
}

export async function showFlowAuthor(ui,{asset,branch=false}={}){
  const {api,esc,content,current,params,tell,formDialog}=ui;
  const prefix=storagePrefix(ui);
  const restored=asset?.restored_nodes?.length?'草稿已恢复运行中取消的环节 '+asset.restored_nodes.join('、')+'。':'';
  const requestedSelection=asset?.run_id?[...new Set(params.getAll('select'))]:[];
  const draftParts=asset?[asset.id,asset.revision]:['new'];
  if(!asset&&params.has('draft'))draftParts.push(params.get('draft'));
  if(branch)draftParts.push('branch');
  if(asset?.run_id)draftParts.push(asset.run_id,asset.plan_revision,asset.operation,asset.selected_node||params.get('node')||'',
    params.get('branch')==='1'?'extend':'edit');
  if(requestedSelection.length)draftParts.push('selection',JSON.stringify([...requestedSelection].sort()));
  const draftKey=prefix+'draft:'+draftParts.join(':');
  const layoutKey=prefix+'layout:'+(asset?.id||'new');
  const saved=readStore(sessionStorage,draftKey,null);
  if(!asset&&params.has('draft')&&!saved?.value){
    content.innerHTML=ui.empty('当前标签页没有这份草稿，可回到工作流库重新导入。');return;
  }
  let value=saved?.value||structuredClone(asset?.definition||blankDefinition());
  if(branch&&!saved)nameFork(value);
  const revision=asset&&!branch?asset.revision:0;
  const operations=()=>Object.keys(value.operations);
  let operation=[params.get('operation'),saved?.operation,operations()[0]].find(key=>operations().includes(key));
  const emptyPlan={nodes:[],result_node:''};
  const plan=()=>value.operations[operation].execution_plan||emptyPlan;
  let selected=[saved?.selected,asset?.selected_node,params.get('node'),plan().nodes[0]?.key].find(key=>plan().nodes.some(node=>node.key===key));
  let selection=(saved?.selection||[selected]).filter(key=>plan().nodes.some(node=>node.key===key));
  const layouts=saved?.layouts||(asset?readStore(localStorage,layoutKey,{}):{});
  const view={};
  let checking=false,validated=null,dirty=Boolean(saved);
  const positions=()=>{
    if(!Object.hasOwn(layouts,operation))Object.defineProperty(layouts,operation,{value:{},enumerable:true});
    return layouts[operation];
  };
  content.innerHTML='<section class="flow-workbench" aria-label="工作流设计器">'
    +'<header class="flow-header"><div><a href="#workflows?view=catalog">工作流</a><h1 data-flow-name></h1></div>'
    +'<div class="actions"><output data-draft-status></output><button type="button" data-flow-settings>工作流设置</button>'
    +'<button type="button" class="primary" data-flow-publish>校验并预览</button></div></header>'
    +'<div class="flow-top flow-edit-bar"><label>操作 <select data-operation>'+operations().map(key=>
      '<option value="'+esc(key)+'">'+esc(key)+'</option>').join('')+'</select></label>'
    +'<div class="actions"><button type="button" data-flow-new>＋ 自定义环节</button>'
    +'<button type="button" data-flow-insert>插入工作流</button>'
    +'<button type="button" data-flow-package disabled>封装为工作流</button></div></div>'
    +'<div class="flow-split"><div data-flow-main></div><aside class="flow-inspector" aria-label="节点属性"></aside></div></section>';
  const host=content.querySelector('[data-flow-main]'),inspector=content.querySelector('.flow-inspector');
  const title=content.querySelector('[data-flow-name]'),status=content.querySelector('[data-draft-status]');
  const publish=content.querySelector('[data-flow-publish]');
  const packageButton=content.querySelector('[data-flow-package]');
  function setSelection(keys){
    selection=[...new Set(keys)];
    selected=selection.at(-1);
    packageButton.disabled=!selection.length;
  }
  function save(){
    dirty=true;validated=null;
    publish.textContent='校验并预览';
    status.textContent=writeStore(sessionStorage,draftKey,{value,layouts,operation,selected,selection})?'草稿已保存在此标签页':'草稿仅在当前页面中';
    title.textContent=value.name||'未命名工作流';
  }
  function storeLayout(){
    if(asset&&!branch)writeStore(localStorage,layoutKey,layouts);
    if(dirty)writeStore(sessionStorage,draftKey,{value,layouts,operation,selected,selection});
  }
  function openSettings(){
    const op=value.operations[operation];
    const props=op.input_schema?.properties||{},media=op.output_kind==='artifact';
    formDialog({title:'工作流设置',caption:'保存到草稿',
      body:'<div class="template-fields"><label>名称<input name="name" required maxlength="120" value="'+esc(value.name)+'"></label>'
        +'<label>工作流标识<input name="id" required '+(revision?'readonly':'')+' value="'+esc(value.id||'workflow-'+crypto.randomUUID().slice(0,8))+'"></label>'
        +'<label>作者版本<input name="version" required value="'+esc(value.version)+'"></label><label>交付文件名<input name="file" required value="'+esc(op.output_file)+'"></label>'
        +'</div><label>操作指令<textarea name="instruction" required>'+esc(op.instruction)+'</textarea></label>'
        +'<p class="muted">当前输入字段 '+esc(Object.keys(props).join('、')||'无')+'</p>'
        +'<label>添加必填文本字段<input name="input_field" pattern="'+idPattern+'" placeholder="例如 ticket"></label>'
        +'<details><summary>输入、交付与资源契约</summary>'
        +Object.entries({input_schema:op.input_schema,...(media?{}:{output_schema:op.output_schema}),resources:value.resources})
          .map(([key,item])=>'<label>'+({input_schema:'输入 Schema',output_schema:'交付 Schema',resources:'模型与工具槽位'})[key]
            +'<textarea name="'+key+'" rows="5">'+esc(JSON.stringify(item||{},null,2))+'</textarea></label>').join('')
        +'<p><a href="#providers" target="_blank" rel="noopener">管理模型与工具</a> · <a href="#organizations" target="_blank" rel="noopener">管理组织与角色</a></p></details>',
      onSubmit(form,modal){
        const fields=form.elements;
        const input=object(fields.input_schema.value),output=media?undefined:object(fields.output_schema.value),resources=object(fields.resources.value);
        const field=fields.input_field.value.trim();
        if(field){
          if(Object.hasOwn(input.properties||{},field))throw Error('输入字段已存在');
          input.type='object';
          input.properties={...input.properties,[field]:{type:'string'}};
          input.required=[...(input.required||[]),field];
        }
        const target=plan().nodes.find(node=>node.key===plan().result_node);
        const file=fields.file.value.trim();
        // Only synchronize the standard single-file Agent output. Other contracts can be edited on the node.
        if(!media&&target&&kindOf(target)==='agent'&&Object.keys(target.outputs||{}).length===1&&Object.hasOwn(target.outputs,op.output_file)){
          target.outputs={[file]:output};
          if(target.expected===op.output_file)target.expected=file;
        }
        Object.assign(op,{instruction:fields.instruction.value.trim(),input_schema:input,...(media?{}:{output_schema:output}),output_file:file});
        Object.assign(value,{name:fields.name.value.trim(),id:fields.id.value.trim(),version:fields.version.value.trim(),resources});
        save();redraw();modal.close();
      },
    });
  }
  function connect(from,to){
    const nodes=plan().nodes,target=nodes.find(node=>node.key===to);
    if(!target||!nodes.some(node=>node.key===from))return;
    if(target.depends_on?.includes(from))return;
    if(introducesCycle(links(nodes),from,to))throw Error('这条连线会形成循环');
    if((target.depends_on||[]).length>=20)throw Error('每个节点最多有 20 个直接依赖');
    target.depends_on=[...(target.depends_on||[]),from];
    setSelection([to]);save();redraw();
  }
  function addNode(from,kind='agent',point){
    const op=value.operations[operation];
    // The first explicit stage converts a dynamically planned operation into a fixed plan.
    const newPlan=op.execution_plan||{result_node:'',nodes:[]},nodes=newPlan.nodes;
    if(nodes.length>=40){tell('每个操作最多 40 个节点',true);return;}
    const source=nodes.find(node=>node.key===from);
    const key=nextNodeKey(nodes);
    const unit=source?.unit||op.unit||value.units?.[0]?.key;

    const first=!nodes.length;
    let node=agentStage(value,op,key,unit,first);
    if(kind==='tool'){
      const entry=Object.entries(value.resources.tools||{})[0];
      if(!entry){tell('请先在工作流设置中声明工具槽位',true);return;}
      const [tool,contract]=entry;
      node={key,kind,unit,tool_ref:tool,arguments:{},description:'调用 '+tool,expected:'按工具契约交付',
        outputs:contract.output_schema?.properties?.files?.properties||{}};
    }
    if(kind==='group'){
      node={key,kind,unit,description:'汇总下级节点交付',expected:'汇总已声明的交付文件'};
      if(source){
        const files=[...Object.keys(source.outputs||{}),...Object.keys(source.artifact_outputs||{}),...(source.exports||[])];
        const parent=nodes.find(item=>item.key===source.parent);
        if(parent)node.parent=parent.key;
        // Preserve files that the outer group already exports from the wrapped child.
        const required=files.filter(file=>parent?.exports?.includes(file));
        node.exports=[...new Set([...required,...files])].slice(0,12);
        source.parent=key;
      }
    }else if(from)node.depends_on=[from];
    if(first)newPlan.result_node=key;
    if(point){
      // Pin the visible cards before adding an edge changes their automatic layout.
      for(const [id,pos] of graph.positions())putPosition(id,pos);
      putPosition(key,freeFlowPosition({x:Math.round(point.x),y:Math.round(point.y-47)},[...graph.positions().values()]));
    }else if(nodes.length&&Object.keys(positions()).length){
      const visible=graph.positions(),anchor=visible.get(from)||[...visible.values()].at(-1);
      putPosition(key,freeFlowPosition({x:anchor.x+284,y:anchor.y},[...visible.values()]));
    }
    nodes.push(node);op.execution_plan=newPlan;setSelection([key]);save();redraw(!point);
    if(first)inspector.querySelector('[data-node-title]')?.focus({preventScroll:true});
  }
  function chooseResult(node){
    const op=value.operations[operation];
    const files=[...Object.keys(node.outputs||{}),...Object.keys(node.artifact_outputs||{})];
    function use(file){
      plan().result_node=node.key;op.output_file=file;
      if(Object.hasOwn(node.outputs||{},file)){
        op.output_schema=node.outputs[file];delete op.output_kind;delete op.artifact_output;
      }else{
        op.output_kind='artifact';op.artifact_output=node.artifact_outputs[file];delete op.output_schema;
      }
      save();redraw();
    }
    if(files.length===1){use(files[0]);return;}
    formDialog({title:'选择最终交付文件',caption:'设为最终交付',
      body:'<label>交付文件<select name="result_file">'+files.map(file=>'<option'+(node.key===plan().result_node
        &&file===op.output_file?' selected':'')+'>'+esc(file)+'</option>').join('')+'</select></label>',
      onSubmit(form,modal){use(form.elements.result_file.value);modal.close();}});
  }
  function editAdvanced(node){
    formDialog({title:'节点契约 · '+node.key,caption:'保存到草稿',
      body:'<label>节点 JSON<textarea name="node" rows="16" spellcheck="false">'+esc(JSON.stringify(node,null,2))+'</textarea></label>',
      onSubmit(form,modal){
        const updated=object(form.elements.node.value);
        if(updated.key!==node.key)throw Error('节点标识由引用它的边使用，请保留原标识');
        const index=plan().nodes.indexOf(node);
        plan().nodes[index]=updated;save();redraw();modal.close();
      },
    });
  }
  function inspect(){
    const nodes=plan().nodes,node=nodes.find(item=>item.key===selected);
    if(selection.length>1){
      inspector.innerHTML='<header><span>画布选区</span><h2>已选 '+selection.length+' 个环节</h2></header>'
        +'<ul class="flow-selected-list">'+selection.map(key=>{const item=nodes.find(n=>n.key===key);
          return '<li><strong>'+esc(item.title||item.description)+'</strong><small>'+esc(key)+'</small></li>';}).join('')+'</ul>'
        +'<button type="button" data-package-selected>封装为工作流</button>';
      inspector.querySelector('button').onclick=openPackage;return;
    }
    if(!node){
      inspector.innerHTML='<header><span>设计</span><h2>工作流画布</h2></header>'
        +'<p class="muted">'+(nodes.length?'选择一个环节编辑属性，按住 Shift 可多选。'
          :value.operations[operation].execution_plan?'添加一个环节，再从右侧连接点拖出下一步。':'此操作由运行时规划。添加第一个环节可改用固定计划。')+'</p>';
      return;
    }
    const result=plan().result_node===node.key;
    const files=Object.keys(node.outputs||{}).length+Object.keys(node.artifact_outputs||{}).length;
    const parents=nodes.filter(item=>item.key!==node.key&&kindOf(item)==='group');
    const deps=node.depends_on||[];
    const refs=nodes.filter(item=>item.key!==node.key);
    inspector.innerHTML='<header><span>'+esc(kinds[kindOf(node)]||'步骤')+'</span><h2>'+esc(node.key)+'</h2></header>'
      +'<label>环节名称<input data-node-title maxlength="120" placeholder="例如 核对资料" value="'+esc(node.title||'')+'"></label>'
      +'<label>步骤指令<textarea data-instruction rows="4" maxlength="12000">'+esc(node.description)+'</textarea></label>'
      +(node.staff?'<details class="flow-execution"><summary>执行岗位与模型</summary>'
        +'<label>执行岗位<input data-role maxlength="100" value="'+esc(node.staff.name)+'"></label>'
        +'<label>岗位职责<textarea data-role-goal rows="2" maxlength="2000">'+esc(node.staff.role)+'</textarea></label>'
        +'<label>模型槽位<select data-role-model><option value="">按能力分配</option>'
        +Object.entries(value.resources?.models||{}).filter(([,slot])=>(node.staff.capabilities||['text'])
          .filter(cap=>!cap.startsWith('tool:')).every(cap=>slot.capabilities?.includes(cap)))
          .map(([key])=>'<option value="'+esc(key)+'"'+(key===node.staff.model?' selected':'')+'>'+esc(key)+'</option>').join('')
        +'</select></label></details>':'')
      +'<label>预期交付<input data-expected maxlength="3000" value="'+esc(node.expected)+'"></label>'
      +(parents.length?'<label>归属汇总节点<select data-parent><option value="">顶层</option>'
        +parents.map(item=>'<option value="'+esc(item.key)+'"'+(node.parent===item.key?' selected':'')+'>'+esc(item.key)+'</option>').join('')+'</select></label>':'')
      +'<fieldset class="flow-dependencies"><legend>上游依赖</legend>'
      +(refs.length?refs.map(item=>'<label><input type="checkbox" data-dependency="'+esc(item.key)+'"'
        +(deps.includes(item.key)?' checked':'')+'> '+esc(item.key)+'</label>').join(''):'<p class="muted">添加第二个节点后可以建立依赖。</p>')+'</fieldset>'
      +'<div class="flow-buttons"><button type="button" data-add-agent>从此节点添加分支</button>'
      +'<button type="button" data-add-tool>添加工具分支</button><button type="button" data-add-group>汇总此节点的交付</button>'
      +'<button type="button" data-result '+(!files||(result&&files===1)?'disabled':'')+'>'+(result?'当前交付节点':'设为最终交付')+'</button>'
      +'<button type="button" data-advanced>节点契约</button><button type="button" data-remove '+(nodes.length>1&&result?'disabled':'')+'>移除此节点</button></div>';
    inspector.querySelector('[data-node-title]').oninput=event=>{
      const title=event.target.value.trim();
      if(title)node.title=title;else delete node.title;
      save();redraw(false,false);
    };
    inspector.querySelector('[data-instruction]').oninput=event=>{node.description=event.target.value;save();redraw(false,false);};
    inspector.querySelector('[data-expected]').oninput=event=>{node.expected=event.target.value;save();};
    const role=inspector.querySelector('[data-role]');
    if(role){
      role.oninput=event=>{node.staff.name=event.target.value;save();};
      inspector.querySelector('[data-role-goal]').oninput=event=>{node.staff.role=event.target.value;save();};
      inspector.querySelector('[data-role-model]').onchange=event=>{
        if(event.target.value)node.staff.model=event.target.value;else delete node.staff.model;
        save();
      };
    }
    const parent=inspector.querySelector('[data-parent]');
    if(parent)parent.onchange=()=>{
      if(parent.value&&introducesCycle(links(nodes).filter(edge=>!(edge.group&&edge.from===node.key)),node.key,parent.value)){
        parent.value=node.parent||'';tell('汇总关系会形成循环',true);return;
      }
      if(parent.value)node.parent=parent.value;else delete node.parent;
      save();redraw();
    };
    for(const box of inspector.querySelectorAll('[data-dependency]'))box.onchange=()=>{
      if(box.checked){
        try{connect(box.dataset.dependency,node.key);}
        catch(error){box.checked=false;tell(error.message,true);}
      }else{node.depends_on=deps.filter(key=>key!==box.dataset.dependency);save();redraw();}
    };
    inspector.querySelector('[data-add-agent]').onclick=()=>addNode(node.key);
    inspector.querySelector('[data-add-tool]').onclick=()=>addNode(node.key,'tool');
    inspector.querySelector('[data-add-group]').onclick=()=>addNode(node.key,'group');
    inspector.querySelector('[data-advanced]').onclick=()=>editAdvanced(node);
    inspector.querySelector('[data-result]').onclick=()=>chooseResult(node);
    inspector.querySelector('[data-remove]').onclick=()=>{
      if(nodes.some(item=>item.parent===node.key||item.depends_on?.includes(node.key)||item.repair_from?.includes(node.key))){
        tell('此节点仍被引用，请先从下游、汇总或返工设置中解除引用',true);return;
      }
      plan().nodes=nodes.filter(item=>item!==node);delete positions()[node.key];
      if(!plan().nodes.length)plan().result_node='';
      setSelection(plan().nodes.length?[plan().nodes[0].key]:[]);save();redraw();
    };
  }
  function openPackage(){
    if(!selection.length)return;
    const keys=[...selection],sourcePlan=plan();
    const nodeName=key=>{
      const node=sourcePlan.nodes.find(item=>item.key===key);
      return node?.title||key;
    };
    const suffix=crypto.randomUUID().slice(0,8);
    const proposedId=(value.id||'workflow').slice(0,64)+'-'+suffix;
    formDialog({title:'封装为工作流',caption:'校验并发布选区',
      body:'<p>将选中的 '+keys.length+' 个环节发布为可独立安装、运行和再次插入的工作流。</p>'
        +(restored?'<p class="muted">'+esc(restored)+'</p>':'')
        +'<label>工作流名称<input name="package_name" required maxlength="120" value="'
        +esc((keys.length===1?nodeName(keys[0]):value.name||'组合')+' 工作流')+'"></label>'
        +'<label>工作流标识<input name="package_id" required maxlength="80" value="'+esc(proposedId)+'"></label>'
        +'<label>最终交付<select name="package_result"></select></label><div data-package-preview aria-live="polite"></div>',
      mount(form,submit){
        const field=form.elements.package_result,preview=form.querySelector('[data-package-preview]');
        function update(){
          try{
            const [resultKey,resultFile]=field.value?JSON.parse(field.value):[];
            const shape=extractWorkflow(value,operation,keys,{id:proposedId,name:'选区',resultKey,resultFile});
            if(!field.options.length){
              field.innerHTML=shape.resultChoices.map(({key,file})=>'<option value="'+esc(JSON.stringify([key,file]))+'">'
                +esc(nodeName(key)+' · '+file)+'</option>').join('');
              const resultOp=Object.values(shape.definition.operations)[0];
              field.value=JSON.stringify([shape.resultKey,resultOp.output_file]);
            }
            const additions=shape.supportKeys.map(nodeName);
            const edgeList=edges=>edges.map(({from,to})=>esc(nodeName(from))+' → '+esc(nodeName(to))).join('、');
            preview.innerHTML='<p>包含 '+shape.definition.operations[operation].execution_plan.nodes.length+' 个环节。</p>'
              +(additions.length?'<p>为保留验收或交付契约，将一并包含 '+esc(additions.join('、'))+'。</p>':'')
              +(shape.incoming.length||shape.outgoing.length?'<details><summary>查看独立运行时解除的外部连线</summary>'
                +(shape.incoming.length?'<p>接入选区：'+edgeList(shape.incoming)+'</p>':'')
                +(shape.outgoing.length?'<p>离开选区：'+edgeList(shape.outgoing)+'</p>':'')+'</details>':'');
            submit.disabled=false;
          }catch(error){preview.textContent=error.message;submit.disabled=true;}
        }
        update();field.onchange=update;
      },
      async onSubmit(form,modal){
        const {package_name,package_id,package_result}=form.elements;
        const [resultKey,resultFile]=package_result.value?JSON.parse(package_result.value):[];
        const extracted=extractWorkflow(value,operation,keys,{id:package_id.value.trim(),name:package_name.value.trim(),resultKey,resultFile});
        const validated=await api('workflows/validate',{method:'POST',value:extracted.definition});
        if(!current())return;
        try{await api('workflows?revision=0',{method:'POST',value:validated.definition});}
        catch(error){if(error.code==='revision_conflict')throw Error('工作流标识已存在，请为选区填写另一个标识');throw error;}
        if(current()){
          modal.close();tell('已发布「'+validated.definition.name+'」。可以在画布中插入这个工作流。');
        }
      },
    });
  }
  async function openInsert(){
    const catalog=await api('workflows');
    if(!current())return;
    const choices=catalog.flatMap(asset=>Object.entries(asset.definition.operations)
      .filter(([,op])=>op.execution_plan?.nodes.length)
      .map(([key,op])=>({asset,key,count:op.execution_plan.nodes.length})));
    if(!choices.length){tell('目录中还没有可插入的固定计划，可以先选中画布环节并封装为工作流。');return;}
    formDialog({title:'插入工作流',caption:'插入到画布',
      body:'<label>工作流与操作<select name="source">'+choices.map(({asset,key,count},index)=>
        '<option value="'+index+'">'+esc(asset.definition.name+' · '+key+' · 修订 '+asset.revision+' · '+count+' 个环节')+'</option>').join('')+'</select></label>'
        +'<label>连接位置<select name="after"><option value="">作为独立分支</option>'
        +plan().nodes.map(node=>'<option value="'+esc(node.key)+'"'+(selection.length===1&&selected===node.key?' selected':'')+'>'
          +esc('接在 '+(node.title||node.key)+' 后面')+'</option>').join('')+'</select></label>'
        +'<p class="muted">此修订会展开为可编辑的环节，内部连线和契约会一起插入。</p>',
      onSubmit(form,modal){
        const {asset,key}=choices[Number(form.elements.source.value)];
        const after=form.elements.after.value||undefined;
        const target=structuredClone(value);
        target.operations[operation].execution_plan??={nodes:[],result_node:''};
        const inserted=insertWorkflow(target,operation,asset.definition,key,{after,starter:blankDefinition()});
        value=inserted.definition;setSelection(inserted.addedKeys);
        save();redraw(true);modal.close();
        tell('已插入 '+inserted.addedKeys.length+' 个环节。');
      },
    });
  }
  function putPosition(key,position){
    Object.defineProperty(positions(),key,{value:position,writable:true,enumerable:true,configurable:true});
  }
  function selectNodes(keys){setSelection(keys);storeLayout();inspect();}
  const graph=mountFlow(host,{signal:ui.signal,state:view,connect,onEmpty:()=>addNode(),add:(key,point)=>addNode(key,'agent',point),
    move(key,position){putPosition(key,position);storeLayout();},
    select:key=>selectNodes([key]),selectMany:selectNodes});
  function redraw(fit=false,showInspector=true){
    graph.setGraph(displayNodes(plan()),links(plan().nodes),{selected,selectedIds:selection,positions:positions(),fit});
    packageButton.disabled=!selection.length;
    publish.disabled=checking||Boolean(value.operations[operation].execution_plan&&!plan().nodes.length);
    if(showInspector)inspect();
  }
  const opSelect=content.querySelector('[data-operation]');
  opSelect.value=operation;
  opSelect.onchange=()=>{
    operation=opSelect.value;setSelection(plan().nodes[0]?[plan().nodes[0].key]:[]);storeLayout();redraw(true);
  };
  content.querySelector('[data-flow-new]').onclick=()=>addNode();
  content.querySelector('[data-flow-insert]').onclick=async event=>{
    const button=event.currentTarget;
    button.disabled=true;
    try{await openInsert();}catch(error){if(current())tell(error.message,true);}
    finally{button.disabled=false;}
  };
  packageButton.onclick=openPackage;
  content.querySelector('[data-flow-settings]').onclick=openSettings;
  title.textContent=value.name||'未命名工作流';
  const missingSource=!asset?.selected_node&&params.has('node')&&!plan().nodes.some(node=>node.key===params.get('node'));
  status.textContent=missingSource?'使用运行冻结的发布定义；此节点只存在于当次执行计划。'
    :dirty?'已恢复此标签页的草稿':(asset?'修订 '+asset.revision:'新建草稿');
  if(restored)status.textContent+=' · '+restored;
  publish.onclick=async()=>{
    if(checking)return;
    if(!value.name||!value.id){openSettings();return;}
    const snapshot=structuredClone(value),signature=JSON.stringify(snapshot);
    checking=publish.disabled=true;
    try{
      if(validated?.signature===signature){
        const result=await api('workflows?revision='+revision,{method:'POST',value:validated.definition});
        if(!current())return;
        writeStore(localStorage,prefix+'layout:'+result.id,layouts);
        const revised=signature!==JSON.stringify(value);
        if(revised&&value.id!==result.id){
          tell('已发布「'+snapshot.name+'」。另一标识下的修改保留在当前草稿。');return;
        }
        const route='workflows/'+encodeURIComponent(result.id);
        if(revised&&!writeStore(sessionStorage,prefix+'draft:'+result.id+':'+result.revision,{value,layouts,operation,selected,selection})){
          tell('工作流已发布。此后编辑的内容仍在当前页面，请先保留草稿。',true);return;
        }
        try{sessionStorage.removeItem(draftKey);}catch{}
        tell(revised?'工作流已发布，后续编辑已保留为下一修订的草稿。':'工作流已发布。可安装此修订后发起运行。');
        location.hash=route+'?view='+(revised?'design':'catalog')+'&revision='+result.revision;
      }else{
        const checked=await api('workflows/validate',{method:'POST',value:snapshot});
        if(!current()||signature!==JSON.stringify(value))return;
        validated={signature,definition:checked.definition};
        status.textContent='校验通过 · '+plan().nodes.length+' 个节点';
        publish.textContent='发布工作流';
      }
    }catch(error){if(current())tell(error.code==='revision_conflict'?'目录已有更新。当前标签页保留了草稿，请核对最新修订。':error.message,true);}
    finally{checking=false;if(current())publish.disabled=Boolean(value.operations[operation].execution_plan&&!plan().nodes.length);}
  };
  redraw();
  if(requestedSelection.length){
    const missing=requestedSelection.filter(key=>!plan().nodes.some(node=>node.key===key));
    if(missing.length)tell('无法在此运行草稿中找到所选环节 '+missing.join('、'),true);
    else{setSelection(requestedSelection);redraw();openPackage();}
  }else if(branch&&asset?.run_id&&params.get('branch')==='1'&&!saved&&selected)addNode(selected);
}
