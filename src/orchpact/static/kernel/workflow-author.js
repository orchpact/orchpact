const keyPattern='[a-zA-Z][a-zA-Z0-9_\\-]{0,55}';

function fieldsSchema(form, selector) {
  const fields=[...form.querySelectorAll(selector)].map(row=>[
    row.querySelector('[data-key]').value.trim(),
    {type:row.querySelector('[data-type]').value},
    row.querySelector('[data-required]').checked,
  ]);
  if(new Set(fields.map(([key])=>key)).size!==fields.length) throw Error('同一契约中的字段标识必须唯一');
  return {type:'object',additionalProperties:false,
    properties:Object.fromEntries(fields.map(([key,type])=>[key,type])),
    required:fields.filter(([, ,required])=>required).map(([key])=>key)};
}

export function openAuthor(ui) {
  const {api,esc,formDialog,tell}=ui;
  let validated;
  formDialog({
    title:'新建固定工作流',caption:'校验并预览',
    body:'<div class="template-fields">'
      +'<label>名称<input name="name" required maxlength="120"></label>'
      +'<label>工作流标识<input name="id" required pattern="'+keyPattern+'" placeholder="example-review"></label>'
      +'<label>作者版本<input name="version" value="1.0.0" required></label>'
      +'<label>操作标识<input name="operation" value="generate" required pattern="'+keyPattern+'"></label>'
      +'<label>执行团队名称<input name="team" required value="交付团队"></label>'
      +'<label>交付文件名<input name="output" required value="result.json"></label>'
      +'<label class="span-all">操作指令<textarea name="instruction" required rows="3"></textarea></label>'
      +'</div><fieldset class="author-section"><legend>输入契约</legend><div data-contract="input"></div>'
      +'<button type="button" data-add-field="input">添加输入字段</button></fieldset>'
      +'<fieldset class="author-section"><legend>交付契约</legend><div data-contract="output"></div>'
      +'<button type="button" data-add-field="output">添加输出字段</button></fieldset>'
      +'<fieldset class="author-section"><legend>固定执行计划</legend><p class="muted">步骤按列表顺序衔接，最后一步生成约定交付。每个步骤声明一个执行岗位，安装时绑定文本模型。</p>'
      +'<div data-nodes></div><button type="button" data-add-node>添加步骤</button></fieldset><div data-author-preview></div>',
    mount(form,submit) {
      let next=1;
      const nodes=form.querySelector('[data-nodes]'), output=form.querySelector('[data-contract="output"]');
      const preview=form.querySelector('[data-author-preview]');
      function invalidate() {
        validated=null;
        submit.textContent='校验并预览';
        preview.replaceChildren();
      }
      function add(container,markup){
        const row=document.createElement('div');
        row.className='template-fields author-row';
        row.innerHTML=markup+'<button class="span-all" type="button" data-remove>移除</button>';
        row.querySelector('[data-remove]').onclick=()=>{
          row.remove();
          invalidate();
        };
        container.append(row);
        invalidate();
      }
      function addField(kind,key=''){
        add(form.querySelector('[data-contract="'+kind+'"]'),
          '<label>字段标识<input data-key value="'+esc(key)+'" required pattern="'+keyPattern+'"></label>'
          +'<label>类型<select data-type><option value="string">文本</option><option value="number">数值</option>'
          +'<option value="integer">整数</option><option value="boolean">布尔</option></select></label>'
          +'<label><input data-required type="checkbox" checked>必填</label>');
      }
      function addNode(){
        add(nodes,'<label>步骤标识<input data-node value="step-'+next+++'" required pattern="'+keyPattern+'"></label>'
          +'<label>执行岗位<input data-role value="交付执行者" required maxlength="120"></label>'
          +'<label class="span-all">步骤指令<textarea data-instruction rows="2" required></textarea></label>');
      }
      for(const button of form.querySelectorAll('[data-add-field]'))button.onclick=()=>addField(button.dataset.addField);
      form.querySelector('[data-add-node]').onclick=addNode;
      form.oninput=invalidate;
      addField('output','summary');
      addNode();
    },
    async onSubmit(form,modal) {
      const formData=Object.fromEntries(new FormData(form)),outputSchema=fieldsSchema(form,'[data-contract="output"] .author-row');
      const rows=[...form.querySelectorAll('[data-nodes] .author-row')];
      if(!rows.length) throw Error('至少保留一个固定步骤');
      if(!Object.keys(outputSchema.properties).length) throw Error('至少声明一个交付字段');
      const nodes=rows.map((row,index)=>{
        const key=row.querySelector('[data-node]').value.trim();
        const name=row.querySelector('[data-role]').value.trim();
        const instruction=row.querySelector('[data-instruction]').value.trim();
        const final=index===rows.length-1;
        const expected=final?formData.output.trim():'step-'+(index+1)+'.json';
        const schema=final?outputSchema:{type:'object',additionalProperties:false,properties:{summary:{type:'string'}},required:['summary']};
        return {key,unit:'entry',description:instruction,expected,
          ...(index?{depends_on:[rows[index-1].querySelector('[data-node]').value.trim()]}:{}),
          outputs:{[expected]:schema}, staff:{name,role:instruction,reason:'执行声明的固定步骤',capabilities:['text','json'],model:'reasoning'}};
      });
      const operation={unit:'entry',instruction:formData.instruction.trim(),input_schema:fieldsSchema(form,'[data-contract="input"] .author-row'),
        output_schema:outputSchema,output_file:formData.output.trim(),
        execution_plan:{result_node:nodes.at(-1).key,nodes}};
      const value={format:'orchpact-workflow',id:formData.id.trim(),name:formData.name.trim(),version:formData.version.trim(),
        resources:{models:{reasoning:{capabilities:['text','json']}}},
        units:[{key:'entry',name:formData.team.trim(),purpose:formData.instruction.trim(),model:'reasoning'}],
        operations:{[formData.operation.trim()]:operation}};
      const signature=JSON.stringify(value);
      if(validated?.signature===signature){
        const published=await api('workflows',{method:'POST',value:validated.definition});
        modal.close();
        tell('新修订已登记');
        location.hash='workflows/'+encodeURIComponent(published.id)+'?view=catalog';
        return;
      }
      const result=await api('workflows/validate',{method:'POST',value});
      validated={signature,definition:result.definition};
      form.querySelector('[data-author-preview]').innerHTML='<div class="panel"><strong>工作流定义校验通过</strong><ol>'
        +nodes.map(node=>'<li>'+esc(node.key)+' · '+esc(node.expected)+'</li>').join('')
        +'</ol><p class="muted">确认后登记新工作流，可在当前空间继续预览安装。</p></div>';
      modal.submit.textContent='发布工作流';
    },
  });
}
