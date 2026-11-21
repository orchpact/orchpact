/* Native template controls; the server remains the JSON Schema authority. */
(function(root,factory){
 const api=factory();
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.OrchpactTemplateForm=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
 'use strict';
 const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
 const clone=value=>value===undefined?undefined:JSON.parse(JSON.stringify(value));
 function applyDefaults(schema,value){
  const result=clone(value)||{};
  for(const [name,child] of Object.entries(schema.properties||{})){
   if(!Object.hasOwn(result,name)&&Object.hasOwn(child,'default'))result[name]=clone(child.default);
   if(Object.hasOwn(result,name)&&child.type==='object'&&result[name]&&typeof result[name]==='object'&&!Array.isArray(result[name]))result[name]=applyDefaults(child,result[name]);
  }
  return result;
 }
 function defaults(schema){return applyDefaults(schema,{});}
 function parseScalar(raw,schema,path){
  const kind=schema.type;
  if(kind==='string')return String(raw);
  if(kind==='boolean'){
   if(raw===true||raw==='true')return true;
   if(raw===false||raw==='false')return false;
   throw Error(path+' 必须是布尔值');
  }
  const text=String(raw).trim(),value=Number(text);
  if(!text||!Number.isFinite(value)||kind==='integer'&&!Number.isInteger(value))throw Error(path+(kind==='integer'?' 必须是整数':' 必须是有限数字'));
  return value;
 }
 function control(root,path){return [...root.querySelectorAll('[data-path]')].find(item=>item.dataset.path===path);}
 function read(root,schema,path='',required=true){
  const result={};
  for(const [name,child] of Object.entries(schema.properties||{})){
   const childPath=path?path+'.'+name:name,isRequired=(schema.required||[]).includes(name);
   if(child.type==='object'){
    const value=read(root,child,childPath,isRequired);
    if(Object.keys(value).length||isRequired)result[name]=value;
    continue;
   }
   const input=control(root,childPath);
   if(!input)throw Error('缺少表单控件 '+childPath);
   let raw=input.type==='checkbox'?input.checked:input.value;
   if(child.type==='array'){
    const values=input.multiple?[...input.selectedOptions].map(option=>option.value):String(raw).split('\n').map(item=>item.trim()).filter(Boolean);
    if(!values.length&&!isRequired)continue;
    result[name]=values.map((value,index)=>parseScalar(value,child.items,childPath+'['+index+']'));
   }else if(child.type==='boolean'){
    if(raw===''&&!isRequired)continue;
    result[name]=parseScalar(raw,child,childPath);
   }else{
    if(String(raw).trim()===''&&!isRequired)continue;
    result[name]=parseScalar(raw,child,childPath);
   }
  }
  return result;
 }
 function errorPath(message){
  const match=String(message).match(/字段([^，]+)/);
  if(!match||match[1]==='<root>')return '';
  return match[1].replace(/^parameters\./,'');
 }
 function showError(root,message){
  for(const item of root.querySelectorAll('[data-path]'))item.removeAttribute('aria-invalid');
  for(const item of root.querySelectorAll('[data-error-for]'))item.textContent='';
  const path=errorPath(message),input=path&&control(root,path),target=path&&root.querySelector('[data-error-for="'+path+'"]');
  if(input)input.setAttribute('aria-invalid','true');
  if(target)target.textContent=message;
  return path;
 }
 function attributes(schema,required){
  const values={minlength:schema.minLength,maxlength:schema.maxLength,title:schema.description};
  if(schema.type==='number'||schema.type==='integer')Object.assign(values,{min:schema.minimum,max:schema.maximum,step:schema.type==='integer'?1:(schema.step??'any')});
  if(schema.type==='string'&&schema.pattern)values.pattern=schema.pattern;
  const result=Object.entries(values).filter(([,value])=>value!==undefined&&value!==null).map(([key,value])=>key+'="'+esc(value)+'"').join(' ');
  return (result?' '+result:'')+(required&&schema.type!=='array'&&schema.type!=='boolean'?' required':'');
 }
 function field(path,name,schema,value,required){
  const title=schema.title||name,description=schema.description?'<small>'+esc(schema.description)+'</small>':'',error='<p class="error template-field-error" data-error-for="'+esc(path)+'" role="alert"></p>';
  if(schema.type==='object'){
   const values=value&&typeof value==='object'&&!Array.isArray(value)?value:{};
   return '<fieldset class="template-object"><legend>'+esc(title)+'</legend>'+description+Object.entries(schema.properties||{}).map(([key,child])=>field(path?path+'.'+key:key,key,child,values[key],(schema.required||[]).includes(key))).join('')+error+'</fieldset>';
  }
  const id='template-'+path.replace(/[^a-zA-Z0-9_-]/g,'-'),attrs=attributes(schema,required),label='<label for="'+esc(id)+'">'+esc(title)+(required?' <span aria-hidden="true">*</span>':'')+'</label>';
  let input;
  if(schema.enum){
   input='<select id="'+esc(id)+'" data-path="'+esc(path)+'"'+attrs+'>'+(!required?'<option value="">未指定</option>':'')+schema.enum.map(item=>'<option value="'+esc(item)+'"'+(String(item)===String(value)?' selected':'')+'>'+esc(item)+'</option>').join('')+'</select>';
  }else if(schema.type==='boolean'){
   if(required)input='<input id="'+esc(id)+'" data-path="'+esc(path)+'" type="checkbox"'+(value===true?' checked':'')+'>';
   else input='<select id="'+esc(id)+'" data-path="'+esc(path)+'"><option value=""'+(value===undefined?' selected':'')+'>未指定</option><option value="true"'+(value===true?' selected':'')+'>是</option><option value="false"'+(value===false?' selected':'')+'>否</option></select>';
  }else if(schema.type==='array'){
   if(schema.items.enum)input='<select id="'+esc(id)+'" data-path="'+esc(path)+'" multiple size="'+Math.min(6,Math.max(2,schema.items.enum.length))+'">'+schema.items.enum.map(item=>'<option value="'+esc(item)+'"'+((value||[]).includes(item)?' selected':'')+'>'+esc(item)+'</option>').join('')+'</select>';
   else input='<textarea id="'+esc(id)+'" data-path="'+esc(path)+'" rows="3" placeholder="每行一个值">'+esc((value||[]).join('\n'))+'</textarea>';
  }else if(schema.type==='string'&&(schema.format==='textarea'||schema.maxLength>200)){
   input='<textarea id="'+esc(id)+'" data-path="'+esc(path)+'" rows="5"'+attrs+'>'+esc(value??'')+'</textarea>';
  }else{
   const kind=schema.type==='integer'||schema.type==='number'?'number':'text';
   input='<input id="'+esc(id)+'" data-path="'+esc(path)+'" type="'+kind+'" value="'+esc(value??'')+'"'+attrs+'>';
  }
  return '<div class="template-field">'+label+input+description+error+'</div>';
 }
 function render(schema,value){return Object.entries(schema.properties||{}).map(([name,child])=>field(name,name,child,value[name],(schema.required||[]).includes(name))).join('');}
 function nodeSummary(node){
  const files=[...Object.keys(node.outputs||{}),...Object.keys(node.artifact_outputs||{})];
  return '<li><strong>'+esc(node.key)+'</strong> <span class="badge">'+esc(node.kind||'agent')+'</span><br><small>依赖 '+esc((node.depends_on||[]).join(' → ')||'无')+' · 文件 '+esc(files.join('、')||node.exports?.join('、')||'无')+'</small></li>';
 }
 function previewHtml(result){
  const definition=result.definition;
  const operations=Object.entries(definition.operations||{}).map(([name,operation])=>'<article class="template-operation"><h3>'+esc(name)+'</h3><p>'+esc(operation.instruction)+'</p><small>交付 '+esc(operation.output_file||'未声明')+'</small>'+(operation.execution_plan?'<ol>'+operation.execution_plan.nodes.map(nodeSummary).join('')+'</ol>':'<p class="muted">动态操作，没有预声明固定节点；运行仍使用现有工作项快照。</p>')+'</article>').join('');
  return '<div class="template-preview"><p><strong>定义 SHA-256</strong><br><code>'+esc(result.sha256)+'</code></p>'+operations+'<details><summary>查看完整规范 JSON</summary><pre>'+esc(JSON.stringify(definition,null,2))+'</pre></details><button type="button" class="primary" data-publish-template>使用此定义登记</button></div>';
 }
 function mount(root,templates,{preview,publish,current=()=>true}){
  let selected=templates[0],saved=null,generation=0;
  function draw(){
   generation++;saved=null;
   root.innerHTML='<div class="row"><div><h2>从固定模板创建</h2><p class="muted">参数由服务端校验。预览不会发布、绑定、安装或执行。</p></div><select data-template-choice aria-label="工作流模板">'+templates.map(item=>'<option value="'+esc(item.template_id)+'"'+(item===selected?' selected':'')+'>'+esc(item.name)+' · '+esc(item.version)+'</option>').join('')+'</select></div><p class="muted">'+esc(selected.description)+'</p><form data-template-form><div class="template-fields">'+render(selected.parameters_schema,defaults(selected.parameters_schema))+'</div><p class="error" data-template-error role="alert"></p><button type="submit" class="primary" data-template-preview>校验并预览</button></form><div data-template-result></div>';
   const form=root.querySelector('[data-template-form]');
   root.querySelector('[data-template-choice]').onchange=event=>{selected=templates.find(item=>item.template_id===event.target.value);draw();};
   form.oninput=()=>{generation++;saved=null;root.querySelector('[data-template-result]').innerHTML='';root.querySelector('[data-template-error]').textContent='';};
   form.onsubmit=async event=>{
    event.preventDefault();const button=form.querySelector('[data-template-preview]');button.disabled=true;
    const token=++generation,template=selected;let parameters;
    try{parameters=read(root,template.parameters_schema);}catch(error){root.querySelector('[data-template-error]').textContent=error.message;button.disabled=false;return;}
    const signature=JSON.stringify(parameters);
    try{
     const result=await preview(template,parameters);
     if(!current()||token!==generation||template!==selected)return;
     saved={result,signature,template};root.querySelector('[data-template-result]').innerHTML=previewHtml(result);
     root.querySelector('[data-publish-template]').onclick=()=>{if(saved&&saved.signature===signature)publish(saved.result.definition);};
    }catch(error){if(current()&&token===generation){root.querySelector('[data-template-error]').textContent=error.message;showError(root,error.message);}}
    finally{if(current()&&token===generation)button.disabled=false;}
   };
  }
  draw();
 }
 return {defaults,parseScalar,read,errorPath,showError,render,previewHtml,mount};
});
