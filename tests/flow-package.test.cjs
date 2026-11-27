const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const helpers=import('../src/orchpact/static/kernel/flow-package.js');
const schema={type:'object'};
function node(key,dependencies=[],file=key+'.json'){
  return {key,unit:'entry',kind:'agent',description:key,expected:file,depends_on:dependencies,
    staff:{name:'Creator',role:'Create',reason:'Create',model:'reasoning',capabilities:['text','json']},outputs:{[file]:schema}};
}
function workflow(nodes,result=nodes.at(-1)?.key){
  return {format:'orchpact-workflow',id:'source',name:'Source',version:'1',units:[{key:'entry',name:'Desk',purpose:'Deliver',model:'reasoning'}],
    resources:{models:{reasoning:{capabilities:['text','json']}}},
    operations:{work:{unit:'entry',instruction:'Do the work',input_schema:{type:'object',properties:{},additionalProperties:false},
      output_file:result+'.json',output_schema:schema,execution_plan:{result_node:result,nodes}}}};
}
const plan=definition=>definition.operations.work.execution_plan;

test('a disconnected selection preserves only internal edges and exposes boundary edges',async()=>{
  const {extractWorkflow}=await helpers;
  const src=workflow([node('before'),node('one',['before']),node('other'),node('after',['one','other'])]);
  const before=structuredClone(src);
  const {definition,incoming,outgoing,resultKey,supportKeys}=extractWorkflow(src,'work',['one','other'],{name:'选区',id:'selection',resultKey:'other'});
  assert.deepEqual(plan(definition).nodes.map(n=>n.key),['one','other']);
  assert.deepEqual(incoming,[{from:'before',to:'one'}]);
  assert.deepEqual(outgoing,[{from:'one',to:'after'},{from:'other',to:'after'}]);
  assert.deepEqual(supportKeys,[]);assert.equal(resultKey,'other');
  assert.equal(definition.operations.work.output_file,'other.json');
  plan(definition).nodes[0].staff.name='Edited';
  assert.deepEqual(src,before);
});
test('extracting an artifact tool keeps typed data references, group exports and least required bindings',async()=>{
  const {extractWorkflow}=await helpers;
  const input=node('source');input.parent='team';
  const group={key:'team',kind:'group',description:'Collect',expected:'source.json',exports:['source.json']};
  const artifact={key:'render',unit:'entry',kind:'tool',tool_ref:'render',arguments:{frame:{$ref:'json',node:'team',file:'source.json',path:''}},
    description:'Render',expected:'clip.png',depends_on:['team'],artifact_outputs:{'media/clip.png':{media_type:'image/png',max_bytes:2048}}};
  const src=workflow([node('other'),input,group,artifact]);
  src.resources.tools={render:{interface:'example.render'},unused:{interface:'unused'}};
  src.resources.models.unused={capabilities:['image']};
  src.units.push({key:'unrelated',name:'Unused',purpose:'Unused',model:'unused'});
  const {definition,resultKey,supportKeys,resultChoices}=extractWorkflow(src,'work',['render']);
  assert.equal(resultKey,'render');assert.deepEqual(supportKeys,['source','team']);
  assert.deepEqual(resultChoices.at(-1),{key:'render',file:'media/clip.png',kind:'artifact'});
  assert.equal(definition.operations.work.output_kind,'artifact');assert.equal(definition.operations.work.output_schema,undefined);
  assert.deepEqual(definition.operations.work.artifact_output,artifact.artifact_outputs['media/clip.png']);
  assert.deepEqual(Object.keys(definition.resources.models),['reasoning']);
  assert.deepEqual(Object.keys(definition.resources.tools),['render']);
  assert.deepEqual(definition.units.map(unit=>unit.key),['entry']);
});
test('a selected gate retains verified inputs and the whole repair path; group inheritance survives extraction',async()=>{
  const {extractWorkflow}=await helpers;
  const root=node('input'),a=node('a',['input']),b=node('b',['a']),check=node('check',['b']);
  Object.assign(check,{gate:true,repair_from:['input']});
  const src=workflow([root,a,b,check], 'input');
  const result=extractWorkflow(src,'work',['check']);
  assert.deepEqual(result.supportKeys,['input','a','b']);assert.equal(result.resultKey,'check');
  const group={key:'wrap',kind:'group',description:'Collect',expected:'x',depends_on:['input']};
  const child=node('child');child.parent='wrap';
  const nested=extractWorkflow(workflow([root,group,child]),'work',['input','child']);
  assert.deepEqual(plan(nested.definition).nodes[1].depends_on,['input']);assert.equal(plan(nested.definition).nodes[1].parent,undefined);
});
test('insertion remaps colliding identifiers in references and role grants while leaving user data alone',async()=>{
  const {insertWorkflow}=await helpers;
  const seed=node('constructor');
  const next={key:'render',kind:'tool',tool_ref:'build',description:'Tool',expected:'out.json',outputs:{'out.json':schema},
    depends_on:['constructor'],arguments:{input:{$ref:'json',node:'constructor',file:'constructor.json',path:''},literal:{node:'constructor',tool_ref:'build'}}};
  const src=workflow([seed,next]),target=workflow([node('constructor'),node('render',['constructor'])]);
  src.resources.tools={build:{interface:'source.tool'}};target.resources.tools={build:{interface:'host.tool'}};
  src.resources.models.reasoning.extra='different';
  seed.staff.capabilities.push('tool:build');
  src.units[0].crew={members:[{key:'constructor',model:'reasoning',capabilities:['text','tool:build']}]};
  const sourceBackup=structuredClone(src),targetBackup=structuredClone(target);
  const result=insertWorkflow(target,'work',src,'work',{after:'render'});
  assert.deepEqual(src,sourceBackup);assert.deepEqual(target,targetBackup);
  assert.deepEqual(result.addedKeys,['constructor-2','render-2']);
  const nodes=plan(result.definition).nodes.slice(2);
  assert.deepEqual(nodes[0].depends_on,['render']);
  assert.deepEqual(nodes[1].depends_on,['constructor-2']);
  assert.equal(nodes[0].unit,'entry-2');
  assert.equal(nodes[0].staff.model,'reasoning-2');assert.equal(nodes[0].staff.capabilities.at(-1),'tool:build-2');
  assert.equal(nodes[1].arguments.input.node,'constructor-2');
  assert.deepEqual(nodes[1].arguments.literal,{node:'constructor',tool_ref:'build'});
  assert.equal(nodes[1].tool_ref,'build-2');
  assert.equal(result.definition.units.at(-1).crew.members[0].key,'constructor');
  assert.equal(result.definition.units.at(-1).crew.members[0].capabilities.at(-1),'tool:build-2');
  assert.equal(plan(result.definition).result_node,'render');
});
test('empty canvas adopts a full operation; insertion in a populated canvas carries acceptance as a scoped group',async()=>{
  const {insertWorkflow,extractWorkflow}=await helpers;
  const src=JSON.parse(fs.readFileSync(path.join(__dirname,'../examples/fulfillment/workflow.json')));
  const opKey=Object.keys(src.operations)[0],op=src.operations[opKey],blank=workflow([]);
  const fresh=insertWorkflow(blank,'work',src,opKey,{starter:blank});
  assert.deepEqual(plan(fresh.definition).nodes.map(n=>n.key),op.execution_plan.nodes.map(n=>n.key));
  assert.deepEqual(fresh.definition.operations.work.verification,op.verification);
  assert.deepEqual(fresh.definition.units,src.units);
  assert.equal(fresh.definition.operations.work.output_file,op.output_file);
  assert.equal(fresh.definition.operations.work.execution_plan.result_node,op.execution_plan.result_node);
  const target=workflow([node('host')]);
  const inserted=insertWorkflow(target,'work',src,opKey,{after:'host'});
  const combined=plan(inserted.definition),accept=combined.nodes.find(n=>n.kind==='group'&&n.verification);
  assert.ok(accept);
  assert.deepEqual(accept.verification,op.verification);
  assert.deepEqual(accept.exports,[op.output_file]);
  assert.equal(combined.nodes.find(n=>n.key===op.execution_plan.result_node).parent,accept.key);
  assert.equal(combined.result_node,'host');
  assert.deepEqual(inserted.definition.operations.work.input_schema.required,op.input_schema.required);
  const release=extractWorkflow(src,opKey,[op.execution_plan.result_node]);
  assert.ok(release.supportKeys.length);assert.deepEqual(release.definition.operations[opKey].verification,op.verification);
});
test('conflicting input definitions and over-limit plans fail without changing the target',async()=>{
  const {insertWorkflow}=await helpers;
  const src=workflow([node('A')]),target=workflow([node('B')]);
  src.operations.work.input_schema.properties.topic={type:'string'};
  target.operations.work.input_schema.properties.topic={type:'number'};
  assert.throws(()=>insertWorkflow(target,'work',src,'work'),/topic/);
  assert.equal(plan(target).nodes.length,1);
  const full=workflow(Array.from({length:40},(_,i)=>node('node'+i)));
  assert.throws(()=>insertWorkflow(full,'work',src,'work'),/40/);
  assert.equal(plan(full).nodes.length,40);
});

test('a customized empty canvas keeps its own resources, instruction and limits',async()=>{
  const {insertWorkflow}=await helpers;
  const starter=workflow([]),edited=structuredClone(starter),source=workflow([node('one')]);
  edited.resources.models.search={capabilities:['search']};
  edited.operations.work.request_limit=3;
  edited.operations.work.instruction='A custom commissioning instruction';
  const {definition}=insertWorkflow(edited,'work',source,'work',{starter});
  assert.deepEqual(definition.resources.models.search,{capabilities:['search']});
  assert.equal(definition.operations.work.request_limit,3);
  assert.equal(definition.operations.work.instruction,edited.operations.work.instruction);
  edited.operations.work.output_file='custom.json';
  assert.throws(()=>insertWorkflow(edited,'work',source,'work',{starter}),/交付契约/);
});
