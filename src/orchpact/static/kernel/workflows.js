import {mountFlow} from './flow-canvas.js';
import {previewFlow,showFlowAuthor,saveCanvasSeed,forkCanvasSeed,canvasDrafts} from './flow-author.js';
import {connectExampleModel} from './settings.js';

const template = globalThis.OrchpactTemplateForm;
const encode = encodeURIComponent;
const workflowPath = id => 'workflows/'+encode(id);

function objectJSON(text) {
  const value = JSON.parse(text);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('请输入 JSON 对象');
  return value;
}

function supportsFields(schema) {
  if (!schema || ![undefined,'object'].includes(schema.type)) return false;
  return Object.entries(schema.properties || {}).every(([key, field]) =>
    /^[A-Za-z0-9_-]+$/.test(key) && key !== '__proto__' &&
    (field.type === 'object' ? supportsFields(field) :
      field.type === 'array' ? scalar(field.items) && !field.enum : scalar(field)));
}
function scalar(field) {
  return ['string','boolean','integer','number'].includes(field?.type)
    && (!field.enum || field.enum.every(value => ['string','boolean','number'].includes(typeof value) && value !== ''));
}

function openRun(ui, workflow) {
  const {esc,api,formDialog} = ui;
  const operations = workflow.operations;
  if (!operations.length) return;
  let operation;
  let structured;
  const options = operations.map(item => '<option value="'+esc(item.key)+'">'+esc(item.key)+'</option>').join('');
  formDialog({
    title:'运行 '+workflow.name,
    caption:'提交运行',
    body:'<p class="muted">已安装修订 '+workflow.workflow_revision+' · 安装修订 '+workflow.installation_revision
      +' · 付款人 '+esc(ui.me.username)+' · <a href="#account?credits=mine" target="_blank" rel="noopener">查看积分</a></p>'
      +'<label>操作<select name="operation">'+options+'</select></label>'
      +'<p data-operation-summary class="muted"></p>'
      +'<label data-run-json hidden><input name="use_json" type="checkbox"> 使用 JSON 输入</label>'
      +'<div data-run-fields class="template-fields"></div>'
      +'<details><summary>高级输入</summary><label>执行上下文 JSON<textarea name="context" rows="3">{}</textarea></label>'
      +'<label>图像请求 JSON（可选）<textarea name="image_request" rows="3"></textarea></label></details>',
    mount(form) {
      const panel = form.querySelector('[data-run-fields]');
      function showOperation(reset = false) {
        operation = operations.find(item => item.key === form.elements.operation.value);
        const schema = operation.input_schema;
        const canUseForm = supportsFields(schema);
        const input = template.defaults(schema);
        if (reset) form.elements.use_json.checked = !canUseForm;
        form.querySelector('[data-run-json]').hidden = !canUseForm;
        structured = canUseForm && !form.elements.use_json.checked;
        const limits = Object.entries(operation.limits).map(([key,value]) =>
          ({request_limit:'模型请求',search_limit:'搜索',tool_limit:'工具'})[key]+' '+value).join(' · ');
        form.querySelector('[data-operation-summary]').textContent =
          [operation.output_file && '约定交付 '+operation.output_file,limits,
            !operation.fixed_plan && '此操作依赖实例开启实验规划'].filter(Boolean).join(' · ');
        panel.innerHTML = structured ? template.render(schema,input) || '<p>此操作无需输入字段。</p>'
          : '<label class="span-all">输入 JSON<textarea name="input" rows="12" spellcheck="false" required>'+esc(JSON.stringify({},null,2))+'</textarea></label>';
      }
      form.elements.operation.onchange = () => showOperation(true);
      form.elements.use_json.onchange = () => showOperation();
      showOperation(true);
    },
    async onSubmit(form,modal) {
      const value = {
        operation:operation.key,
        input:structured ? template.read(form,operation.input_schema) : objectJSON(form.elements.input.value),
        context:objectJSON(form.elements.context.value),
      };
      if (form.elements.image_request.value.trim()) value.image_request = objectJSON(form.elements.image_request.value);
      const run = await api(workflowPath(workflow.workflow_id)+'/runs?installation_revision='+workflow.installation_revision,{
        method:'POST',value,headers:{'Idempotency-Key':modal.keyFor(value)},
      });
      if (!ui.current()) return;
      modal.close();
      location.hash = 'projects/'+encode(run.id);
    },
  });
}

async function openInstall(ui, workflow, revision) {
  const {api,esc,formDialog,current,tell} = ui;
  const path = workflowPath(workflow);
  let installed;
  try { installed=await api(path+'/installation'); }
  catch(error) { if (error.status !== 404) throw error; }
  const options=await api('workbench/binding-options/'+encode(workflow)+(revision ? '?revision='+revision : ''));
  if (!current()) return;
  const selectable = {models:Object.entries(options.model_options),tools:Object.entries(options.tool_options)};
  function renderOptions(kind, groups) {
    return groups.map(([slot, choices],index) => {
      const saved=installed?.bindings?.[kind]?.[slot];
      const active=kind==='models' ? choice => choice.id===saved :
        choice => choice.endpoint_ref===saved?.endpoint_ref && choice.implementation_version===saved?.implementation_version;
      const selected=choices.findIndex(active);
      return '<label>'+esc(slot)+'<select name="'+kind+index+'" required><option value="">'
        +(choices.length?'选择本地资源':'当前没有可用资源')+'</option>'
        +choices.map((item,i) => '<option value="'+i+'"'+(i===selected || choices.length===1?' selected':'')+'>'
          +esc(item.label+' · '+(item.id || item.endpoint_ref+' / '+item.implementation_version))+'</option>').join('')
        +'</select><small>'+esc(options.requirements[kind][slot])+'</small></label>';
    }).join('');
  }
  let preview = null, change = 0;
  const bindingFields = renderOptions('models',selectable.models)+renderOptions('tools',selectable.tools);
  formDialog({
    title:(installed?'升级或重新绑定 · ':'安装 · ')+options.name,
    caption:'预览资源',
    body:'<p>目录修订 '+options.workflow_revision+(installed?' · 当前已安装 '+installed.workflow_revision:'')+'</p>'
      +'<div class="template-fields">'+(bindingFields || '<p>此修订未声明需要绑定的资源。</p>')+'</div><div data-binding-preview></div>',
    mount(form,submit) {
      form.oninput=() => {
        change++;
        preview=null;
        submit.textContent='预览资源';
        form.querySelector('[data-binding-preview]').replaceChildren();
      };
    },
    async onSubmit(form,modal) {
      const bindings={models:{}};
      if (selectable.tools.length || installed?.bindings?.tools) bindings.tools={};
      for (const [kind,groups] of Object.entries(selectable)) {
        groups.forEach(([slot,choices],i) => {
          const choice=choices[form.elements[kind+i].value];
          if (!choice) throw Error('请选择资源 '+slot);
          bindings[kind][slot]=kind==='models' ? choice.id :
            {endpoint_ref:choice.endpoint_ref,implementation_version:choice.implementation_version};
        });
      }
      const value={workflow_revision:options.workflow_revision,bindings};
      const signature=JSON.stringify(value);
      if (preview?.signature===signature) {
        try {
          await api(path+'/install',{method:'POST',value:{...value,expected_revision:preview.result.installed_revision}});
        } catch(error) {
          if(error.code==='revision_conflict'){
            preview=null;
            modal.submit.textContent='重新预览资源';
            form.querySelector('[data-binding-preview]').replaceChildren();
          }
          throw error;
        }
        if (!current()) return;
        modal.close();
        const destination = '#workflows/'+encode(workflow)+'?view=canvas';
        if (location.hash === destination) await ui.render();
        else location.hash = destination;
        tell('已安装，可以在画布上发起运行。');
        return;
      }
      const generation=change;
      const result=await api(path+'/preview',{method:'POST',value});
      if (generation!==change || !current()) return;
      preview={signature,result};
      const label={unchanged:'当前安装相同',install:'安装到当前空间',upgrade:'更新当前安装'};
      const bindingName=(kind, value)=>kind==='models' ? value : value.endpoint_ref+' / '+value.implementation_version;
      const changes=Object.entries(bindings).flatMap(([kind,values])=>Object.entries(values).map(([slot,value])=>{
        const before=installed?.bindings?.[kind]?.[slot];
        const next=bindingName(kind,value);
        return slot+' · '+(before?bindingName(kind,before)+' → ':'')+next;
      }));
      const summary=form.querySelector('[data-binding-preview]');
      summary.innerHTML='<div class="panel"><strong>'+esc(label[result.action])+'</strong><p>可运行操作：'
        +esc(result.operations.join('、'))+'</p><p class="muted">当前安装修订 '+result.installed_revision
        +' · 确认后才更新空间。</p>'+ (changes.length?'<ul>'+changes.map(change=>'<li>'+esc(change)+'</li>').join('')+'</ul>':'')+'</div>';
      modal.submit.textContent='确认安装';
    },
  });
}

function catalogueCards(ui, assets) {
  const {esc}=ui;
  return assets.map(asset => '<article class="panel workflow-card"><div class="row"><h2>'+esc(asset.definition.name)
    +'</h2><span class="badge">目录修订 '+asset.revision+'</span></div><p class="muted">'+esc(asset.id+' · '+asset.version)
    +'</p><div class="actions"><a class="action-link" href="#workflows/'+encode(asset.id)+'?view=catalog">打开定义画布</a>'
    +'<button data-install="'+esc(asset.id)+'" data-revision="'+asset.revision+'">安装到当前空间</button></div></article>').join('');
}

async function showCatalogDetail(ui) {
  const {api,current,content,key,heading,esc,params}=ui;
  const rev=Number(params.get('revision')) || null;
  const path=workflowPath(key);
  const [asset,history]=await Promise.all([api(path+(rev?'?revision='+rev:'')),api(path+'/revisions')]);
  if (!current()) return;
  content.innerHTML=heading(asset.definition.name,'平台目录 · '+asset.id+' · 目录修订 '+asset.revision,
    '<button class="primary" data-install="'+esc(asset.id)+'" data-revision="'+asset.revision+'">安装此修订</button>')
    +'<div class="actions"><a class="action-link" href="#workflows?view=catalog">返回平台目录</a>'
    +'<a class="action-link" href="/api/v1/'+path+'/definition?revision='+asset.revision+'">导出此修订</a>'
    +'<a class="action-link" href="#workflows/'+encode(key)+'?view=design&amp;revision='+asset.revision
    +(asset.revision!==history[0].revision?'&amp;fork=1':'')+'">在画布中编辑</a></div>'
    +'<div class="studio-preview" data-catalog-graph></div>'
    +'<details class="panel"><summary>修订记录</summary><ul>'+history.map(item=>'<li><a href="#workflows/'+encode(key)
    +'?view=catalog&amp;revision='+item.revision+'">修订 '+item.revision+' · '+esc(item.version)+'</a></li>').join('')+'</ul></details>'
    +'<details class="panel"><summary>完整操作与契约</summary>'+template.previewHtml(asset)+'</details>';
  previewFlow(content.querySelector('[data-catalog-graph]'),ui,asset.definition,
    previewActions(ui,()=>asset.definition));
  content.querySelector('[data-publish-template]')?.remove();

}

const examplePath = id => 'workbench/examples/'+encode(id);

function inputSummary(example, sample) {
  const orders=sample.input.orders?.length;
  const warehouses=sample.input.warehouses?.length;
  if(example.id==='fulfillment')return orders+' 笔订单 · '+warehouses+' 座仓库 · 自带库存与运输规则';
  return '自带人物、线索和故事设定';
}

function examplePrices(session, modelId) {
  const rate=session.provider_prices.find(provider=>provider.id===modelId)?.pricing;
  if (!rate) return '每 '+session.credit_policy.tokens_per_point+' Token 计 1 积分';
  return rate.unit==='request' ? '每次请求 '+rate.points+' 积分'
    : '每 '+rate.unit_size+' Token 计 '+rate.points+' 积分';
}

async function openExampleRun(ui,example){
  const {api,esc,formDialog,current}=ui;
  const endpoint=examplePath(example.id),path=workflowPath(example.workflow_id);
  const [sample, models, session, installed]=await Promise.all([
    api(endpoint),api('models'),api('auth/me'),
    api(path+'/installation').catch(error=>{if(error.status!==404)throw error;return null;}),
  ]);
  if(!current())return;
  const slots=Object.entries(sample.definition.resources.models);
  const choices=slots.map(([slot, requirement])=>({
    slot, models:models.filter(model=>requirement.capabilities.every(capability=>model.capabilities.includes(capability))),
  }));
  if(installed&&installed.workflow_revision!==example.revision){
    throw Error('当前空间使用了其他修订，请在平台目录核对后再运行内置示例。');
  }
  if(choices.some(group=>!group.models.length)){
    return connectExampleModel(ui,()=>openExampleRun(ui,example));
  }
  const settings='<div class="template-fields">'+choices.map(({slot,models:available},i)=>{
    const saved=available.find(model=>model.id===installed?.bindings.models[slot]);
    const selected=saved?.id ?? (available.length===1 ? available[0].id : '');
    return '<label>'+(choices.length===1?'文本模型':'文本模型 · '+esc(slot))
      +'<select name="model_'+i+'" required><option value="">请选择模型</option>'
      +available.map(model=>'<option value="'+esc(model.id)+'"'+(model.id===selected?' selected':'')+'>'
        +esc(model.label)+'</option>').join('')+'</select></label>';
  }).join('')+'</div>';
  const credits=session.credits.available;
  const needsCredits=credits<=0;
  let expected=installed?.revision ?? 0;
  let creditKey, creditDelta, credited=false;

  formDialog({
    title:'运行 '+example.name,
    caption:needsCredits?'补充积分并运行':'确认运行',
    body:'<p>'+esc(inputSummary(example,sample))+'</p>'+settings
      +'<p class="muted" data-example-price></p><p class="muted">付款账号 '+esc(session.username)+' · 可用 '
      +esc(credits)+' 积分。积分是平台内部额度，模型供应商可能另外计费。</p>'
      +(needsCredits?'<label>添加到自己账号的积分<input name="credits" type="number" min="1" max="1000000000"'
        +' step="1" value="1000" required></label>':'')
      +'<p class="muted">'+(installed?'更换模型会更新当前空间的示例安装。':'确认后会在当前空间安装并运行示例。')
      +(example.human_review?' 故事完成后需要在后台人工验收。':'')+'</p>'
      +'<details><summary>查看或修改示例输入</summary><label>输入 JSON<textarea name="sample_input" rows="10"'
      +' spellcheck="false">'+esc(JSON.stringify(sample.input,null,2))+'</textarea></label></details>',
    mount(form){
      function showPrice(){
        form.querySelector('[data-example-price]').textContent=choices.map(({slot},i)=>{
          const id=form.elements['model_'+i].value;
          return id ? (choices.length===1?'':slot+' · ')+examplePrices(session,id) : '';
        }).filter(Boolean).join('；');
      }
      form.oninput=showPrice;
      showPrice();
    },
    async onSubmit(form,modal){
      const bindings={models:Object.fromEntries(choices.map(({slot},i)=>[slot,form.elements['model_'+i].value])),tools:{}};
      const value={operation:sample.operation,input:objectJSON(form.elements.sample_input.value),context:{}};
      const idempotencyKey=modal.keyFor(value);
      const delta=needsCredits ? creditDelta ?? Number(form.elements.credits.value) : 0;
      const latest=await api('auth/me');
      if(!current())return;
      if(Object.values(bindings.models).some(id=>examplePrices(latest,id)!==examplePrices(session,id))){
        throw Error('模型积分计价已更新，请关闭后重新打开示例。');
      }
      const prepared=await api(endpoint+'/prepare',{method:'POST'});
      if(!current())return;
      const options=await api('workbench/binding-options/'+encode(example.workflow_id)+'?revision='+prepared.revision);
      if(!current())return;
      for(const [slot,model] of Object.entries(bindings.models)){
        if(!options.model_options[slot]?.some(choice=>choice.id===model))throw Error('所选模型已不可用，请重新打开示例。');
      }
      for(const [slot,tools] of Object.entries(options.tool_options)){
        const tool=tools.find(choice=>choice.endpoint_ref===prepared.provider_id);
        if(!tool)throw Error('示例校验器不可用，请重新打开示例。');
        bindings.tools[slot]={endpoint_ref:tool.endpoint_ref,implementation_version:tool.implementation_version};
      }
      const setup={workflow_revision:prepared.revision,bindings};
      const preview=await api(path+'/preview',{method:'POST',value:setup});
      if(!current())return;
      if(preview.installed_revision!==expected && preview.action!=='unchanged'){
        throw Error('当前空间的安装已变化，请重新打开示例。');
      }
      const installation=await api(endpoint+'/install',{method:'POST',value:{...setup,expected_revision:preview.installed_revision}});
      expected=installation.revision;
      if(!current())return;
      if(needsCredits&&!credited){
        creditDelta=delta;
        creditKey ??= crypto.randomUUID();
        form.elements.credits.readOnly=true;
        await api('admin/users/'+encode(session.user_id)+'/credits',{method:'POST',
          value:{delta,reason:'内置示例试用额度'},headers:{'Idempotency-Key':creditKey}});
        credited=true;
        modal.submit.textContent='确认运行';
      }
      if(!current())return;
      const run=await api(path+'/runs?installation_revision='+installation.revision,{
        method:'POST',value,headers:{'Idempotency-Key':idempotencyKey},
      });
      if(!current())return;
      modal.close();
      location.hash='projects/'+encode(run.id)+'?follow=1';
    },
  });
}

export async function showCanvasHome(ui) {
  if (ui.me.admin) return showFlowAuthor(ui);
  const installed = await ui.api('workbench/installations');
  if (!ui.current()) return;
  if (installed.length) return showInstalledCanvas(ui,installed[0],installed);
  ui.content.innerHTML = ui.heading('工作画布','当前空间还没有已安装的工作流。',
    '<a class="action-link" href="#workflows">打开工作流库</a>')+'<div class="studio-preview" data-empty-canvas></div>';
  const graph = mountFlow(ui.content.querySelector('[data-empty-canvas]'),{signal:ui.signal});
  graph.setGraph([],[]);
}

function previewActions(ui, source) {
  if (!ui.me.admin) return {};
  let opening = false;
  return {async branch(node, operation) {
    if (opening) return;
    opening = true;
    try {
      const definition = await source();
      if (ui.current()) location.hash = forkCanvasSeed(ui,definition,node,operation);
    } catch (error) { if (ui.current()) ui.tell(error.message,true); }
    finally { opening = false; }
  }};
}

async function showInstalledCanvas(ui, workflow, installations) {
  const {api,current,content,key,heading,esc} = ui;
  const id = workflow?.workflow_id || key;
  const [asset, available] = await Promise.all([
    api('workbench/installations/'+encode(id)+'/definition'), installations || api('workbench/installations'),
  ]);
  if (!current()) return;
  const item = available.find(entry=>entry.workflow_id === id);
  if (!item || item.workflow_revision !== asset.revision) throw Error('安装已变化，请刷新画布');
  const catalogLink = ui.me.admin ? '<a class="action-link" href="#workflows/'+encode(id)+'?view=catalog&amp;revision='
    +asset.revision+'">查看发布修订</a>' : '';
  content.innerHTML = heading(asset.definition.name,'已安装 · 修订 '+asset.revision,
    '<div class="actions">'+catalogLink+'<button type="button" class="primary" data-run-installed>发起运行</button></div>')
    +'<div class="studio-preview" data-installed-graph></div>';
  previewFlow(content.querySelector('[data-installed-graph]'),ui,asset.definition,
    previewActions(ui,async()=> (await api(workflowPath(id)+'?revision='+asset.revision)).definition));
  content.querySelector('[data-run-installed]').onclick = ()=>openRun(ui,item);
}

async function showExampleCanvas(ui) {
  const {api,content,current,esc,heading,key} = ui;
  const [detail, examples] = await Promise.all([api(examplePath(key)),api('workbench/examples')]);
  if (!current()) return;
  const example = examples.find(item=>item.id === key);
  if (!example) throw Error('未找到这个示例');
  content.innerHTML = heading(example.name,example.description,
    '<div class="actions"><a class="action-link" href="#workflows?view=examples">内置示例</a>'
      +(!example.conflict?'<button type="button" class="primary" data-start-example>运行示例</button>':'')+'</div>')
    +'<div class="studio-preview" data-example-graph></div><details class="panel"><summary>示例输入</summary><pre>'
    +esc(JSON.stringify(detail.input,null,2))+'</pre></details>';
  previewFlow(content.querySelector('[data-example-graph]'),ui,detail.definition,
    previewActions(ui,()=>detail.definition));
  const button = content.querySelector('[data-start-example]');
  if (button) button.onclick = async () => {
    button.disabled = true;
    try { await openExampleRun(ui,example); }
    catch (error) { if (current()) ui.tell(error.message,true); }
    finally { if (current()) button.disabled = false; }
  };
}

async function showExamples(ui, tabs) {
  const {api,current,content,heading,esc,empty}=ui;
  const examples = await api('workbench/examples');
  if (!current()) return;
  content.innerHTML = heading('内置示例','')+tabs
    +'<div class="workflow-grid">'+(examples.map(example => {
      const state = example.installed_revision ? '当前空间已安装' : example.revision ? '已发布到目录' : '待安装';
      return '<article class="panel workflow-card scenario-card"><div class="row"><h2>'+esc(example.name)
        +'</h2><span class="badge">'+esc(state)+'</span></div><p>'+esc(example.description)
        +'</p><ol class="scenario-stages">'+example.highlights.map(stage=>'<li>'+esc(stage)+'</li>').join('')
        +'</ol><div class="actions"><a class="action-link" href="#workflows/'+encode(example.id)
        +'?view=example">打开示例画布</a></div></article>';
    }).join('')||empty('没有可用的内置示例。'))+'</div>';

}

export async function showWorkflows(ui) {
  const {api,current,content,heading,esc,empty,tell,me,owner,params,key}=ui;
  const safely=action=>async event=>{try{await action(event);}catch(error){if(current())tell(error.message,true);}};
  const catalog=me.admin && params.get('view')==='catalog';
  const examples=me.admin && params.get('view')==='examples';
  const selected = active => active ? ' aria-current="page"' : '';
  const tabs='<div class="section-tabs"><a href="#workflows"'+selected(!catalog && !examples)+'>已安装</a>'
    +(me.admin?'<a href="#workflows?view=catalog"'+selected(catalog)+'>平台目录</a>'
      +'<a href="#workflows?view=examples"'+selected(examples)+'>内置示例</a>':'')+'</div>';
  if(me.admin&&params.get('view')==='example'&&key) return showExampleCanvas(ui);
  if(me.admin&&params.get('view')==='design'){
    const revision=Number(params.get('revision'));
    let asset = null;
    if (key && params.has('run') && params.has('fork')) {
      const query = new URLSearchParams();
      for (const field of ['plan_revision','node']) if (params.has(field)) query.set(field,params.get(field));
      for (const node of params.getAll('select')) query.append('select',node);
      asset = await api('workbench/runs/'+encode(params.get('run'))+'/draft?'+query);
      if (asset.id !== key || asset.revision !== revision || asset.operation !== params.get('operation')) {
        throw Error('此画布链接与运行使用的工作流修订不一致');
      }
    } else if (key) asset = await api(workflowPath(key)+(revision?'?revision='+revision:''));

    if(current())await showFlowAuthor(ui,{asset,branch:!!asset&&params.has('fork')});
    return;
  }
  if(key&&params.get('view')==='canvas')return showInstalledCanvas(ui);
  if(key&&!catalog) { content.innerHTML=empty('当前身份或链接无法打开这个定义。'); return; }
  if (examples) await showExamples(ui,tabs);
  else if (catalog && key) await showCatalogDetail(ui);
  else if (catalog) {
    const [assets,templates]=await Promise.all([api('workflows'),api('workflow-templates')]);
    if (!current()) return;
    content.innerHTML=heading('平台目录','',
      '<div class="actions"><button id="import-workflow">导入 JSON</button><button type="button" class="primary" data-new-canvas>新建工作流</button></div>')
      +tabs+'<label>查找目录工作流<input id="catalog-filter" type="search" placeholder="名称或标识"></label>'
      +'<div id="catalog-cards" class="workflow-grid">'+(catalogueCards(ui,assets)||empty('平台目录还没有工作流。'))+'</div>'
      +'<details class="panel"><summary>从模板创建</summary>'
      +'<section class="template-creator" id="template-creator"></section></details>';
    function bindInstall() {
      for (const button of content.querySelectorAll('[data-install]')) {
        button.onclick=safely(()=>openInstall(ui,button.dataset.install,Number(button.dataset.revision)));
      }
    }
    content.querySelector('#catalog-filter').oninput=event=>{
      const q=event.target.value.trim().toLowerCase();
      const filtered=assets.filter(a=>[a.id,a.definition.name].some(value=>value.toLowerCase().includes(q)));
      content.querySelector('#catalog-cards').innerHTML=catalogueCards(ui,filtered)||empty('没有符合条件的工作流。');
      bindInstall();
    };
    content.querySelector('#import-workflow').onclick=()=>ui.formDialog({
      title:'导入到画布',caption:'在画布中打开',
      body:'<label>工作流定义 JSON<textarea name="definition" rows="13" required spellcheck="false"></textarea></label>',
      async onSubmit(form,modal) {
        const value=objectJSON(form.elements.definition.value);
        const checked=await api('workflows/validate',{method:'POST',value});
        if (!current()) return;
        const destination=saveCanvasSeed(ui,checked.definition);
        modal.close();location.hash=destination;
      },
    });
    if(templates.length) template.mount(content.querySelector('#template-creator'),templates,{current,
      preview:(item,parameters)=>api('workflow-templates/'+encode(item.template_id)+'/preview',{method:'POST',value:{sha256:item.sha256,parameters}}),
      publish:safely(definition=>{location.hash=saveCanvasSeed(ui,definition);})});
    else content.querySelector('#template-creator').remove();
  } else {
    const installed=await api('workbench/installations');
    if (!current()) return;
    const drafts = me.admin ? canvasDrafts(ui) : [];
    const local = drafts.length ? '<section class="local-drafts"><h2>此标签页的草稿</h2>'
      +drafts.map(draft=>'<a href="'+esc(draft.href)+'">'+esc(draft.name)+'</a>').join('')+'</section>' : '';
    content.innerHTML=heading('已安装','打开工作流画布查看步骤或发起运行。',
      '<div class="actions">'+(me.admin?'<button type="button" class="primary" data-new-canvas>新建工作流</button>':'')
      +(owner?'<button type="button" id="install-by-id">按标识安装</button>':'')+'</div>')+tabs+local
      +'<div class="workflow-grid">'+(installed.map(w=>'<article class="panel workflow-card"><div class="row"><h2>'+esc(w.name)
        +'</h2><span class="badge">目录修订 '+w.workflow_revision+'</span></div><p class="muted">'+esc(w.workflow_id)
        +' · 安装 #'+w.installation_revision+(w.latest_revision>w.workflow_revision?' · 有新修订 '+w.latest_revision:'')
        +'</p><p>'+esc(w.operations.map(item=>item.key).join('、'))+'</p><div class="actions">'
        +'<a class="action-link primary" href="#workflows/'+encode(w.workflow_id)+'?view=canvas">打开画布</a>'
        +(owner?'<button data-install="'+esc(w.workflow_id)+'" data-revision="'+w.latest_revision+'">预览安装变更</button>':'')
        +'</div></article>').join('') || empty(owner?'当前空间还没有工作流。可以使用管理员提供的工作流标识安装。':'当前空间还没有工作流，请联系空间所有者。'))+'</div>';
    const byId=content.querySelector('#install-by-id');
    if (byId) byId.onclick=()=>ui.formDialog({
      title:'按标识安装工作流',caption:'继续',body:'<label>工作流标识<input name="workflow" required maxlength="80"></label>',
      onSubmit:async (form,modal)=>{
        const name=form.elements.workflow.value.trim();
        modal.close();
        try { await openInstall(ui,name); } catch(error) { if(current()) tell(error.message,true); }
      },
    });
  }
  if (!current()) return;
  const create = content.querySelector('[data-new-canvas]');
  if (create) create.onclick = safely(()=>{location.hash=saveCanvasSeed(ui);});
  for (const button of content.querySelectorAll('[data-install]')) {
    button.onclick=safely(()=>openInstall(ui,button.dataset.install,Number(button.dataset.revision)));
  }
}
