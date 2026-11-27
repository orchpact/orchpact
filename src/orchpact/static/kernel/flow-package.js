// Compose portable authoring definitions only. Installation bindings and runtime IDs never enter a draft.
const copy=structuredClone;
const unique=items=>[...new Set(items)];
const own=(value,key)=>value!=null&&Object.hasOwn(value,key);
const lookup=(value,key)=>own(value,key)?value[key]:undefined;
const kind=node=>node.kind||(node.staff||node.participants?.length?'agent':'group');
const paths=node=>[...Object.keys(node.outputs||{}),...Object.keys(node.artifact_outputs||{}),...(node.exports||[])];
function fixed(definition,key){
  const op=lookup(definition?.operations,key);
  if(!Array.isArray(op?.execution_plan?.nodes))throw Error('仅带有固定节点的操作可以组合');
  return op;
}
function catalog(nodes){
  const byKey=new Map(nodes.map(node=>[node.key,node]));
  if(byKey.size!==nodes.length)throw Error('节点标识重复，请先修正画布');
  return byKey;
}
function need(index,key){
  if(!index.has(key))throw Error('找不到节点 '+key);
  return index.get(key);
}
function visitRefs(value,apply){
  if(!value||typeof value!=='object')return;
  if(own(value,'$ref')){
    if(value.$ref==='json'||value.$ref==='artifact')apply(value);
    return;
  }
  Object.values(value).forEach(item=>visitRefs(item,apply));
}
function dependencies(node,index){
  const deps=new Set(),seen=new Set();
  while(node){
    if(seen.has(node.key))throw Error('节点归属存在循环');
    seen.add(node.key);
    (node.depends_on||[]).forEach(key=>deps.add(key));
    node=node.parent?need(index,node.parent):null;
  }
  return [...deps];
}
function graphEdges(nodes){
  return nodes.flatMap(node=>[
    ...(node.depends_on||[]).map(from=>({from,to:node.key})),
    ...(node.parent?[{from:node.key,to:node.parent}]:[]),
  ]);
}
// A selected group includes its children. Explicit delivery references, repair paths and
// verifier inputs must travel with the selection even when the user picked a single card.
function supportClosure(nodes,chosen,verifiedResult){
  const index=catalog(nodes), kept=new Set(), parents=new Map();
  for(const node of nodes){
    if(!node.parent)continue;
    if(!parents.has(node.parent))parents.set(node.parent,[]);
    parents.get(node.parent).push(node.key);
  }
  const prerequisites=key=>[...dependencies(need(index,key),index),...(parents.get(key)||[])];
  function retainPath(key,target,seen){
    if(key===target){include(key);return true;}
    if(seen.has(key))return false;
    seen.add(key);
    // Any one original path preserves the rework target's upstream relationship.
    if(prerequisites(key).some(upstream=>retainPath(upstream,target,seen))){include(key);return true;}
    return false;
  }
  function include(key){
    const node=need(index,key);
    if(kept.has(key))return;
    kept.add(key);
    const deps=dependencies(node,index);
    for(const child of parents.get(key)||[])include(child);
    if(node.key===verifiedResult||node.verification||node.gate||node.rules?.some(rule=>rule.kind==='current_inputs')){
      deps.forEach(include);
    }
    visitRefs(node.arguments,ref=>include(ref.node));
    for(const rule of node.rules||[]){
      if(rule.target_file&&!own(node.outputs,rule.target_file)){
        deps.filter(source=>paths(need(index,source)).includes(rule.target_file)).forEach(include);
      }
    }
    for(const upstream of node.repair_from||[]){
      if(!retainPath(key,upstream,new Set()))throw Error('返工目标不在节点上游 '+upstream);
    }
    // The server permits inherited prerequisites across several group levels.
    if(deps.length>20&&node.parent)include(node.parent);
  }
  chosen.forEach(include);
  return kept;
}
function outputChoices(nodes){
  return nodes.flatMap(node=>[
    ...Object.keys(node.outputs||{}).map(file=>({key:node.key,file,kind:'json'})),
    ...Object.keys(node.artifact_outputs||{}).map(file=>({key:node.key,file,kind:'artifact'})),
  ]);
}
function portableResources(definition,operation,nodes){
  const allUnits=definition.units?.length?definition.units:[{key:'entry',name:definition.name||'工作团队',purpose:definition.description||definition.name||'执行工作流'}];
  const unitsByKey=new Map(allUnits.map(unit=>[unit.key,unit]));
  const needed=new Set(),models=new Set(),tools=new Set();
  const defaultUnit=operation.unit||allUnits[0].key;
  function addUnit(key){
    while(key&&!needed.has(key)){
      const unit=unitsByKey.get(key);
      if(!unit)throw Error('工作流缺少团队声明 '+key);
      needed.add(key);key=unit.parent;
    }
  }
  function addRole(role){
    if(!role)return;
    if(role.model)models.add(role.model);
    for(const cap of role.capabilities||[])if(cap.startsWith('tool:'))tools.add(cap.slice(5));
  }
  addUnit(defaultUnit);
  for(const node of nodes){
    addUnit(node.unit||defaultUnit);addRole(node.staff);
    if(node.tool_ref)tools.add(node.tool_ref);
    if(node.verification)tools.add(node.verification.tool_ref);
  }
  if(operation.verification)tools.add(operation.verification.tool_ref);
  const units=allUnits.filter(unit=>needed.has(unit.key));
  const agents=(definition.agents||[]).filter(agent=>needed.has(agent.unit));
  for(const unit of units){
    addRole(unit);(unit.crew?.members||[]).forEach(addRole);
  }
  agents.forEach(addRole);
  function slots(collection,keys){
    return Object.fromEntries([...keys].map(key=>{
      if(!own(collection,key))throw Error('缺少资源槽位 '+key);
      return [key,collection[key]];
    }));
  }
  const resources={models:slots(definition.resources?.models,models)};
  if(tools.size)resources.tools=slots(definition.resources?.tools,tools);
  return {units,agents,resources};
}
export function extractWorkflow(definition,operationKey,selectedKeys,options={}){
  const source=fixed(definition,operationKey),original=source.execution_plan;
  const chosen=unique(selectedKeys), index=catalog(original.nodes);
  if(!chosen.length)throw Error('请先选择至少一个节点');
  const kept=supportClosure(original.nodes,chosen,source.verification?original.result_node:null);
  const nodes=original.nodes.filter(node=>kept.has(node.key)).map(node=>{
    const clone=copy(node);
    if(!kept.has(node.parent))delete clone.parent;
    // Retain selected inherited dependencies if the original enclosing group was left outside.
    let owner=node;const deps=new Set(node.depends_on||[]);
    while(owner.parent&&!kept.has(owner.parent)){
      owner=need(index,owner.parent);
      (owner.depends_on||[]).forEach(key=>deps.add(key));
    }
    clone.depends_on=[...deps].filter(key=>kept.has(key));
    return clone;
  });
  const choices=outputChoices(nodes);
  const resultKey=options.resultKey, resultFile=options.resultFile;
  const originalFile=source.output_file||'result.json';
  const exact=choices.find(item=>item.key===original.result_node&&item.file===originalFile);
  const edges=graphEdges(nodes), sinks=new Set(nodes.map(node=>node.key));
  edges.forEach(edge=>sinks.delete(edge.from));
  let result;
  if(resultKey||resultFile){
    result=choices.find(item=>(!resultKey||item.key===resultKey)&&(!resultFile||item.file===resultFile));
    if(!result)throw Error('此节点没有可作为最终交付的声明文件');
  }else{
    result=(exact&&chosen.includes(exact.key)?exact:null)||choices.find(item=>chosen.includes(item.key)&&sinks.has(item.key))
      ||choices.find(item=>chosen.includes(item.key))||choices[0];
  }
  if(!result)throw Error('选区中需要有一个声明交付文件的执行节点');
  const output=need(index,result.key), operation=copy(source);
  operation.execution_plan={...operation.execution_plan,nodes,result_node:result.key};
  operation.output_file=result.file;
  delete operation.output_schema;delete operation.artifact_output;delete operation.output_kind;
  if(result.kind==='json')operation.output_schema=copy(output.outputs[result.file]);
  else{
    operation.output_kind='artifact';
    operation.artifact_output=copy(output.artifact_outputs[result.file]);
  }
  if(result.key!==original.result_node||result.file!==originalFile)delete operation.verification;
  const resources=portableResources(definition,operation,nodes);
  const packaged=copy({format:'orchpact-workflow',id:options.id??'',name:options.name??'',version:'1.0.0',
    ...resources,operations:{[operationKey]:operation}});
  if(!packaged.agents.length)delete packaged.agents;
  const boundary=graphEdges(original.nodes);
  for(const node of nodes){
    for(const upstream of dependencies(need(index,node.key),index)){
      if(!kept.has(upstream)&&!boundary.some(edge=>edge.from===upstream&&edge.to===node.key)){
        boundary.push({from:upstream,to:node.key});
      }
    }
  }
  return {definition:packaged,selectedKeys:chosen,supportKeys:nodes.map(node=>node.key).filter(key=>!chosen.includes(key)),
    incoming:boundary.filter(edge=>!kept.has(edge.from)&&kept.has(edge.to)),
    outgoing:boundary.filter(edge=>kept.has(edge.from)&&!kept.has(edge.to)),
    resultKey:result.key,resultChoices:choices};
}
function available(key,used){
  let value=key,ordinal=1;
  while(used.has(value)){
    const suffix='-'+(++ordinal);
    value=key.slice(0,64-suffix.length)+suffix;
  }
  used.add(value);return value;
}
function canonical(value){
  if(Array.isArray(value))return value.map(canonical);
  if(!value||typeof value!=='object')return value;
  return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));
}
const equal=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
function mergeInputs(original={},addition={}){
  if(equal(original,addition)||!Object.keys(addition).length)return copy(original);
  if(!Object.keys(original).length)return copy(addition);
  const {properties:left={},required:requiredLeft=[],$defs:leftDefs={},...base}=original;
  const {properties:right={},required:requiredRight=[],$defs:rightDefs={},...extra}=addition;
  const simpleKeys=new Set(['type','additionalProperties','description','title']);
  for(const key of unique([...Object.keys(base),...Object.keys(extra)])){
    if(!simpleKeys.has(key)&&!equal(base[key],extra[key]))throw Error('输入 Schema 的 '+key+' 约束不同，请先统一两侧契约');
  }
  if((base.type&&base.type!=='object')||(extra.type&&extra.type!=='object'))throw Error('请先统一两个操作的输入 Schema');
  if(typeof base.additionalProperties==='object'||typeof extra.additionalProperties==='object'){
    if(!equal(base.additionalProperties,extra.additionalProperties))throw Error('附加输入字段的 Schema 不一致');
  }
  function merge(left,right){
    for(const key of Object.keys(right))if(own(left,key)&&!equal(left[key],right[key]))throw Error('输入契约同名且定义不同 '+key);
    return {...left,...right};
  }
  const result={...extra,...base,type:'object',properties:merge(left,right)};
  if(base.additionalProperties===false||extra.additionalProperties===false)result.additionalProperties=false;
  const required=unique([...requiredLeft,...requiredRight]);
  if(required.length)result.required=required;
  if(Object.keys(leftDefs).length||Object.keys(rightDefs).length)result.$defs=merge(leftDefs,rightDefs);
  return result;
}
function mergeSlots(target,source){
  const used=new Set(Object.keys(target));
  const mapped=new Map();
  for(const [key,value] of Object.entries(source||{})){
    const alias=own(target,key)&&equal(target[key],value)?key:available(key,used);
    Object.defineProperty(target,alias,{value:copy(value),writable:true,configurable:true,enumerable:true});
    mapped.set(key,alias);
  }
  return mapped;
}
// A nested operation with root verification needs a real group candidate and an independent
// PASS receipt. Its enclosing group exports exactly the original operation's result file.
function wrapAcceptance(nodes,resultKey,operation,unit,used){
  const key=available('accept',used),file=operation.output_file||'result.json',byKey=catalog(nodes);
  let child=need(byKey,resultKey);
  while(child.parent){
    const group=need(byKey,child.parent);
    group.exports=unique([...(group.exports||[]),file]);
    if(group.exports.length>12)throw Error('汇总节点交付超过 12 个文件 '+group.key);
    if(nodes.some(other=>other.key!==child.key&&other.parent===group.key&&paths(other).includes(file))){
      throw Error('汇总中存在多个同名最终交付 '+file+'，请先明确唯一来源');
    }
    child=group;
  }
  const top=nodes.filter(node=>!node.parent);
  const duplicate=top.filter(node=>node!==child&&paths(node).includes(file));
  // Hide identically named sibling outputs behind an empty-export group to keep acceptance unambiguous.
  if(duplicate.length){
    const collect=available('other-outputs',used);
    duplicate.forEach(node=>{node.parent=collect;});
    nodes.push({key:collect,kind:'group',parent:key,unit,description:'等待其他支线完成',expected:'全部支线已验收'});
  }
  for(const root of top)if(!root.parent)root.parent=key;
  nodes.push({key,kind:'group',unit,description:operation.instruction,expected:file,exports:[file],verification:operation.verification});
}
export function insertWorkflow(target,operationKey,source,sourceOperationKey,{after,starter}={}){
  const definition=copy(target),host=fixed(definition,operationKey),plan=host.execution_plan;
  const incoming=copy(fixed(source,sourceOperationKey)), graph=incoming.execution_plan;
  if(!graph.nodes.length)throw Error('来源工作流没有可插入的节点');
  const existing=catalog(plan.nodes),blank=existing.size===0;
  if(after!=null)need(existing,after);
  const {units,agents,resources}=portableResources(source,incoming,graph.nodes);
  const sourceUnit=incoming.unit||units[0].key;
  // A fresh single-operation canvas has only the starter team's defaults. An inserted
  // tool-only workflow must remain installable without an unused default model binding.
  const defaults=lookup(starter?.operations,operationKey)??Object.values(starter?.operations||{})[0]??{};
  const untouchedResources=equal(definition.resources,starter?.resources)&&equal(definition.units,starter?.units)
    &&equal(definition.agents,starter?.agents)&&equal(host.unit,defaults.unit);
  if(blank&&starter&&untouchedResources&&Object.keys(definition.operations).length===1){
    definition.units=[];definition.resources={models:{}};delete definition.agents;
  }
  const targetSlots=definition.resources??={models:{}};
  const models=mergeSlots(targetSlots.models??={},resources.models);
  const tools=resources.tools?mergeSlots(targetSlots.tools??={},resources.tools):new Map();
  const targetUnits=definition.units??=[];
  const unitNames=new Set(targetUnits.map(unit=>unit.key)),unitMap=new Map();
  for(const unit of units)unitMap.set(unit.key,available(unit.key,unitNames));
  function role(value){
    if(!value)return;
    if(value.model)value.model=models.get(value.model);
    if(value.capabilities)value.capabilities=value.capabilities.map(cap=>cap.startsWith('tool:')?'tool:'+tools.get(cap.slice(5)):cap);
  }
  for(const unit of copy(units)){
    unit.key=unitMap.get(unit.key);
    if(unit.parent)unit.parent=unitMap.get(unit.parent);
    role(unit);(unit.crew?.members||[]).forEach(role);targetUnits.push(unit);
  }
  for(const agent of copy(agents)){
    agent.unit=unitMap.get(agent.unit);role(agent);(definition.agents??=[]).push(agent);
  }
  const nodeNames=new Set(existing.keys()),alias=new Map();
  for(const node of graph.nodes)alias.set(node.key,available(node.key,nodeNames));
  const remap=key=>need(alias,key);
  function verification(value){if(value)value.tool_ref=tools.get(value.tool_ref);}
  for(const node of graph.nodes){
    node.key=remap(node.key);node.unit=unitMap.get(node.unit||sourceUnit);
    if(node.parent)node.parent=remap(node.parent);
    node.depends_on=(node.depends_on||[]).map(remap);
    if(after&&!node.parent&&!node.depends_on.length)node.depends_on.push(after);
    if(node.repair_from)node.repair_from=node.repair_from.map(remap);
    visitRefs(node.arguments,ref=>{ref.node=remap(ref.node);});
    if(!blank&&kind(node)==='agent'&&incoming.instruction!==host.instruction){
      node.description=incoming.instruction+'\n\n'+node.description;
      if(node.description.length>12000)throw Error('合并操作指令后超过节点字数上限 '+node.key);
    }
    role(node.staff);
    if(node.tool_ref)node.tool_ref=tools.get(node.tool_ref);
    verification(node.verification);
  }
  verification(incoming.verification);
  incoming.unit=unitMap.get(sourceUnit);
  graph.result_node=remap(graph.result_node);
  const input=mergeInputs(host.input_schema,incoming.input_schema);
  if(!blank&&incoming.verification)wrapAcceptance(graph.nodes,graph.result_node,incoming,incoming.unit,nodeNames);
  if(blank){
    // Preserve contracts that the author already changed on an otherwise empty canvas.
    const edited=key=>own(host,key)&&!equal(host[key],defaults[key]);
    const outputKeys=['output_file','output_kind','artifact_output','output_schema'];
    if(outputKeys.some(key=>edited(key)&&!equal(host[key],incoming[key]))){
      throw Error('当前画布已设置不同的最终交付契约，请统一后再插入');
    }
    if(edited('verification')&&incoming.verification&&!equal(host.verification,incoming.verification)){
      wrapAcceptance(graph.nodes,graph.result_node,incoming,incoming.unit,nodeNames);
    }
    for(const key of ['unit','instruction','verification','request_limit','search_limit','tool_limit','delivery_context_limit']){
      if(edited(key))incoming[key]=copy(host[key]);
    }
    for(const key of ['max_repairs','staffing']){
      if(own(plan,key)&&!equal(plan[key],defaults.execution_plan?.[key]))graph[key]=plan[key];
    }
    definition.operations[operationKey]={...incoming,input_schema:input};
  }else{
    plan.nodes.push(...graph.nodes);host.input_schema=input;
  }
  if((blank?graph.nodes:plan.nodes).length>40)throw Error('组合后的操作超过 40 个节点');
  if(targetUnits.length>48)throw Error('组合后的工作流超过 48 个团队');
  return {definition,addedKeys:graph.nodes.map(node=>node.key),resultKey:graph.result_node};
}
