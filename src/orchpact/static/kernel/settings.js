const categories={text:'文本模型',image:'图片模型',search:'搜索',tool:'工具与验证器'};
const time=timestamp=>new Date(timestamp*1000).toLocaleString('zh-CN',{hour12:false});

const textProvider = {
  category:'text',enabled:true,adapter:'openai_compatible',priority:100,
  search:false,thinking_disabled:false,image_options:{},pricing:null,
};

function saveProvider(api,result,row,creating){
  const providers=creating?[...result.value.providers,row]:result.value.providers.map(item=>item.id===row.id?row:item);
  return api('admin/configuration',{method:'PUT',value:{revision:result.revision,value:{...result.value,providers}}});
}

export function connectExampleModel(ui,resume){
  const {api,formDialog,current}=ui;
  const id='text-'+crypto.randomUUID();
  formDialog({
    title:'连接文本模型',caption:'保存并继续',
    body:'<p class="muted">使用支持 OpenAI 兼容接口的文本模型。密钥保存到此实例。</p>'
      +'<label>接口地址<input type="url" name="base_url" required placeholder="https://api.example.com/v1"></label>'
      +'<label>模型名<input name="model" required maxlength="120" placeholder="填写供应商提供的模型 ID"></label>'
      +'<label>API 密钥<input name="secret" type="password" required autocomplete="new-password" maxlength="4096"></label>',
    async onSubmit(form,modal){
      const {base_url,model,secret}=form.elements;
      const name=model.value.trim();
      const result=await api('admin/configuration');
      if(!current())return;
      if(!result.value.providers.some(provider=>provider.id===id)){
        await saveProvider(api,result,{...textProvider,id,label:name,model:name,
          base_url:base_url.value.trim(),secret:secret.value},true);
      }
      secret.value='';
      if(!current())return;
      modal.close();
      try{await resume();}catch(error){if(current())ui.tell(error.message,true);}
    },
  });
}

function editProvider(ui,result,provider,creating=false){
  const {esc,api,formDialog,render,tell}=ui;
  const input=(key,label,attrs='')=>'<label>'+label+'<input name="'+key+'" value="'+esc(provider[key])+'" '+attrs+'></label>';
  const pricing=provider.pricing||{};
  const tokenRate=provider.category==='text'||provider.category==='image';
  formDialog({title:creating?'添加文本模型':'编辑提供者 · '+provider.label,caption:'保存配置',
    body:'<p class="muted">全平台 · '+esc(provider.adapter)+'。新价格用于后续调用。</p>'
      +'<div class="template-fields">'+(creating?input('id','本地提供者标识','required maxlength="80"'):'')
      +input('label','显示名称','required maxlength="120"')
      +input('priority','优先级','type="number" min="0" max="10000" required')
      +(provider.adapter==='builtin'?'':input('base_url','HTTPS 地址','type="url" required'))
      +(tokenRate?input('model','模型标识','required maxlength="200"'):'')
      +'<label>新密钥<input name="secret" type="password" autocomplete="new-password" maxlength="4096" placeholder="留空保留当前凭据"></label>'
      +(provider.secondary_configured?'<label>新次级密钥<input name="secondary_secret" type="password" autocomplete="new-password" maxlength="4096" placeholder="留空保留当前凭据"></label>':'')
      +'<label><input name="enabled" type="checkbox"'+(provider.enabled?' checked':'')+'>启用</label>'
      +'<label>计价单位<select name="unit"><option value="">使用默认费率</option><option value="request"'
      +(pricing.unit==='request'?' selected':'')+'>请求</option>'
      +(tokenRate?'<option value="tokens"'+(pricing.unit==='tokens'?' selected':'')+'>Token</option>':'')
      +'</select></label><label>积分<input name="points" type="number" step="0.000001" min="0" value="'
      +esc(pricing.points??'')+'"></label><label>计量基数<input name="unit_size" type="number" min="1" max="1000000000" value="'
      +esc(pricing.unit_size??(tokenRate?1000:1))+'"></label></div>',
    async onSubmit(form,modal){
      const source=form.elements;
      const row={...provider,label:source.label.value.trim(),priority:Number(source.priority.value),enabled:source.enabled.checked};
      if(creating)row.id=source.id.value.trim();
      for(const key of ['base_url','model'])if(source[key])row[key]=source[key].value.trim();
      if(source.secret.value)row.secret=source.secret.value;
      if(source.secondary_secret?.value)row.secondary_secret=source.secondary_secret.value;
      const unit=source.unit.value;
      if(unit&&!source.points.value)throw Error('请填写计费积分');
      row.pricing=unit?{unit,points:Number(source.points.value),unit_size:unit==='request'?1:Number(source.unit_size.value)}:null;
      const saved=await saveProvider(api,result,row,creating);
      modal.close();
      tell(saved.restart_required?'已保存，执行并发配置将在重启后生效。':'已保存提供者配置。');
      await render();
    },
  });
}

export async function showProviders(ui){
  const {api,content,heading,esc,params,current,jsonDialog,tell}=ui;
  const result=await api('admin/configuration');
  if(!current())return;
  const category=params.get('category');
  const providers=result.value.providers.filter(p=>!category||p.category===category);
  content.innerHTML=heading('模型与工具','全平台 · 管理模型、搜索、工具实现和费用。配置修订 '+result.revision,
    '<div class="actions"><button class="primary" id="add-model">添加文本模型</button><button id="configuration-history">配置历史</button><button id="advanced-configuration">高级配置</button></div>')
    +(result.restart_required?'<div class="panel muted">执行并发的变更需要重启后生效。</div>':'')
    +'<div class="section-tabs"><a href="#providers"'+(!category?' aria-current="page"':'')+'>全部</a>'
    +Object.entries(categories).map(([key,label])=>'<a href="#providers?category='+key+'"'+(category===key?' aria-current="page"':'')
      +'>'+esc(label)+'</a>').join('')+'</div>'
    +'<div class="workflow-grid">'+(providers.map(p=>'<article class="panel"><div class="row"><h2>'+esc(p.label)
      +'</h2><span class="badge">'+(p.available?'可用':p.enabled?'配置未就绪':'已停用')+'</span></div>'
      +'<p>'+esc(p.model || p.tool_interface || p.adapter)+'</p><p class="muted">'+esc(categories[p.category])
      +' · '+esc(p.id)+(p.implementation_version?' · 实现 '+esc(p.implementation_version):'')+'</p>'
      +'<p>'+(p.pricing?esc(p.pricing.points+' 积分 / '+p.pricing.unit_size+' '+p.pricing.unit):'默认费率')+'</p>'
      +'<button data-provider="'+esc(p.id)+'">编辑</button></article>').join('')||ui.empty('此分类暂无提供者。'))+'</div>';
  for(const button of content.querySelectorAll('[data-provider]')){
    button.onclick=()=>editProvider(ui,result,result.value.providers.find(p=>p.id===button.dataset.provider));
  }
  content.querySelector('#add-model').onclick=()=>editProvider(ui,result,{
    ...textProvider,id:'',label:'',base_url:'',model:'',
  },true);
  content.querySelector('#advanced-configuration').onclick=()=>jsonDialog('高级资源配置',result.value,
    '添加提供者、配置搜索策略、工具接口和验证器。secret 字段仅用于提交新的凭据。',
    value=>api('admin/configuration',{method:'PUT',value:{revision:result.revision,value}}));
  content.querySelector('#configuration-history').onclick=async event=>{
    const button=event.currentTarget;
    button.disabled=true;
    try {
      const history=await api('admin/configuration/history');
      if(current())content.insertAdjacentHTML('beforeend','<section class="panel"><h2>配置历史</h2>'
        +(history.map(item=>'<details><summary>修订 '+item.revision+' · '+esc(time(item.created))+'</summary><pre>'
          +esc(item.summary)+'</pre></details>').join('')||'<p>尚无配置修改记录。</p>')+'</section>');
    } catch(error){if(current()){tell(error.message,true);button.disabled=false;}}
  };
}

export async function showPolicy(ui){
  const {api,workspace,heading,content,current,esc,formDialog,render,tell,params}=ui;
  const platform=params.get('scope')==='platform';
  const scope=platform?'platform':'workspace:'+workspace();
  const path='policies/'+encodeURIComponent(scope);
  const result=await api(path);
  if(!current())return;
  const policy=result.policy;
  content.innerHTML=heading('治理规则',(platform?'全平台':'当前 Workspace')+' · 修订 '+result.revision,
    '<button id="edit-policy" class="primary">编辑规则</button>')
    +'<div class="section-tabs"><a href="#policies"'+(!platform?' aria-current="page"':'')+'>当前空间</a>'
    +'<a href="#policies?scope=platform"'+(platform?' aria-current="page"':'')+'>全平台</a></div>'
    +'<section class="panel"><dl class="run-facts"><div><dt>预算上限</dt><dd>'+esc(policy.budget_points??'未设置')
    +'</dd></div><div><dt>最大交付字节</dt><dd>'+esc(policy.max_output_bytes??'未设置')
    +'</dd></div><div><dt>独立验收</dt><dd>'+(policy.independent_review?'要求':'未设置')+'</dd></div></dl>'
    +'<details><summary>输入和汇总产物契约</summary><pre>'+esc(JSON.stringify({
      input_schema:policy.input_schema,leader_outputs:policy.leader_outputs,
    },null,2))+'</pre></details></section>';
  content.querySelector('#edit-policy').onclick=()=>formDialog({title:'编辑治理规则',
    body:'<div class="template-fields"><label>预算上限（积分）<input name="budget" type="number" min="0" step="1" value="'
      +esc(policy.budget_points)+'" placeholder="留空取消此范围上限"></label><label>最大交付体积（字节）'
      +'<input name="bytes" type="number" min="1" max="10485760" step="1" value="'+esc(policy.max_output_bytes)
      +'"></label><label class="span-all"><input name="review" type="checkbox"'+(policy.independent_review?' checked':'')
      +'>要求独立验收</label></div><details><summary>高级契约</summary>'
      +'<label>输入 Schema<textarea name="input" rows="5">'+esc(JSON.stringify(policy.input_schema,null,2))
      +'</textarea></label><label>汇总产物 Schema<textarea name="output" rows="5">'
      +esc(JSON.stringify(policy.leader_outputs,null,2))+'</textarea></label></details>',
    async onSubmit(form,modal){
      const fields=form.elements;
      const value={input_schema:JSON.parse(fields.input.value),leader_outputs:JSON.parse(fields.output.value),
        budget_points:fields.budget.value===''?null:Number(fields.budget.value),
        max_output_bytes:fields.bytes.value===''?null:Number(fields.bytes.value),independent_review:fields.review.checked};
      await api(path,{method:'PUT',value:{revision:result.revision,policy:value}});
      modal.close();
      tell('治理规则已保存');
      await render();
    },
  });
}

export async function showAudit(ui){
  const {api,heading,content,current,esc,params}=ui;
  const result=await api('admin/audit?'+params.toString());
  if(!current())return;
  const next=new URLSearchParams(params);
  if(result.next_cursor)next.set('cursor',result.next_cursor);
  content.innerHTML=heading('操作审计','全平台 · 查看已记录的账号、空间和管理操作。')
    +'<form id="audit-filter" class="panel filter-bar"><label>动作<input name="action" value="'+esc(params.get('action'))
    +'"></label><label>操作者 ID<input name="actor_id" value="'+esc(params.get('actor_id'))
    +'"></label><label>Workspace ID<input name="workspace_id" value="'+esc(params.get('workspace_id'))
    +'"></label><button>筛选</button></form>'
    +(result.items.length?'<div class="panel table-scroll"><table><thead><tr><th>时间</th><th>动作</th><th>操作者</th><th>对象</th></tr></thead><tbody>'
      +result.items.map(item=>'<tr><td>'+esc(time(item.created))+'</td><td>'+esc(item.action)+'</td><td>'
        +esc(item.actor_id)+'</td><td>'+esc(item.target_type+' · '+item.target_id)
        +'<details><summary>变更摘要</summary><pre>'+esc(JSON.stringify({before:item.before,after:item.after},null,2))
        +'</pre></details></td></tr>').join('')+'</tbody></table></div>':ui.empty('暂无符合条件的操作记录。'))
    +(result.next_cursor?'<a class="action-link" href="#audit?'+esc(next.toString())+'">下一页 →</a>':'');
  content.querySelector('#audit-filter').onsubmit=event=>{
    event.preventDefault();
    const filters=new URLSearchParams([...new FormData(event.target)].filter(([,value])=>value.trim()));
    location.hash='audit'+(filters.size?'?'+filters:'');
  };
}

export async function showInstance(ui){
  const instance=await ui.api('admin/instance');
  if(!ui.current())return;
  const facts=[['Orchpact',instance.version],['PostgreSQL',instance.database_version],
    ['就绪状态',instance.ready?'就绪':'未就绪'],['执行线程',instance.worker_enabled?instance.workers:'已关闭']];
  ui.content.innerHTML=ui.heading('实例状态','全平台 · 查看运行版本、执行队列和维护入口。')
    +'<section class="panel"><h2>运行环境</h2><dl class="run-facts">'+facts.map(([key,value])=>
      '<div><dt>'+ui.esc(key)+'</dt><dd>'+ui.esc(value)+'</dd></div>').join('')+'</dl></section>'
    +'<section class="panel"><h2>工作项队列</h2><dl class="run-facts">'+Object.entries(instance.active).map(([key,count])=>
      '<div><dt>'+ui.esc(ui.label[key]||key)+'</dt><dd>'+ui.esc(count)+'</dd></div>').join('')+'</dl></section>'
    +'<a class="action-link" href="https://orchpact.github.io/docs/tutorials/backup/">备份与恢复步骤 ↗</a>';
}
