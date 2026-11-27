const {test}=require('node:test');
const assert=require('node:assert/strict');

test('branches, disconnected nodes and groups produce a deterministic layout',async()=>{
  const {arrangeFlow}=await import('../src/orchpact/static/kernel/flow-canvas.js');
  const nodes=['join','start','upper','lower','separate'].map(id=>({id}));
  const layout=arrangeFlow(nodes,[
    {from:'start',to:'upper'},{from:'start',to:'lower'},
    {from:'upper',to:'join'},{from:'lower',to:'join',group:true},
  ]);
  assert.equal(layout.size,nodes.length);
  assert.ok(layout.get('start').x<layout.get('upper').x);
  assert.ok(layout.get('lower').x<layout.get('join').x);
  assert.notEqual(layout.get('upper').y,layout.get('lower').y);
  assert.notEqual(layout.get('start').y,layout.get('separate').y);
});
test('cycle checks include group completion edges, and cyclic drafts remain visible',async()=>{
  const {arrangeFlow,introducesCycle}=await import('../src/orchpact/static/kernel/flow-canvas.js');
  const edges=[{from:'child',to:'group',group:true},{from:'group',to:'end'}];
  assert.equal(introducesCycle(edges,'end','child'),true);
  assert.equal(introducesCycle(edges,'other','child'),false);
  const nodes=['child','group','end'].map(id=>({id}));
  assert.equal(arrangeFlow(nodes,[...edges,{from:'end',to:'child'}]).size,3);
});

test('box selection includes intersecting cards in either drag direction',async()=>{
  const {selectionInBox}=await import('../src/orchpact/static/kernel/flow-canvas.js');
  const positions=new Map([['left',{x:-300,y:60}],['middle',{x:-20,y:0}],['below',{x:10,y:115}]]);
  assert.deepEqual(selectionInBox(positions,{x:-180,y:20},{x:2,y:85}),['left','middle']);
  assert.deepEqual(selectionInBox(positions,{x:2,y:85},{x:-180,y:20}),['left','middle']);
  assert.deepEqual(selectionInBox(positions,{x:500,y:500},{x:510,y:510}),[]);
});

test('inserted or reconnected cards avoid manually placed cards and one another',async()=>{
  const {positionFlow}=await import('../src/orchpact/static/kernel/flow-canvas.js');
  const nodes=['source','pinned','added-a','added-b'].map(id=>({id}));
  const edges=['added-a','added-b'].map(to=>({from:'source',to}));
  const pinned={pinned:{x:284,y:5},source:{x:-46,y:77}};
  const placed=positionFlow(nodes,edges,pinned);
  for(const [id,pt] of Object.entries(pinned))assert.deepEqual(placed.get(id),pt);
  for(const [id,a] of placed)for(const [other,b] of placed){
    if(id!==other)assert.ok(Math.abs(a.x-b.x)>=216||Math.abs(a.y-b.y)>=94,id+' overlaps '+other);
  }
  assert.deepEqual(positionFlow(nodes,edges,pinned),placed);
});

test('seeded canvas drafts keep distinct routes and are scoped to user and workspace',async t=>{
  const {canvasDrafts,saveCanvasSeed,forkCanvasSeed}=await import('../src/orchpact/static/kernel/flow-author.js');
  const records=new Map();
  const storage={get length(){return records.size;},key:index=>[...records.keys()][index],
    getItem:key=>records.get(key)??null,setItem:(key,value)=>records.set(key,value)};
  const prior=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage');
  Object.defineProperty(globalThis,'sessionStorage',{value:storage,configurable:true});
  t.after(()=>{if(prior)Object.defineProperty(globalThis,'sessionStorage',prior);else delete globalThis.sessionStorage;});
  const ui=(user,workspace)=>({me:{user_id:user},workspace:()=>workspace});
  const owner=ui('alpha','studio'),otherUser=ui('beta','studio'),otherSpace=ui('alpha','labs');
  const seed=name=>({format:'orchpact-workflow',name});
  const route=saveCanvasSeed(owner,seed('本地资料整理'));
  assert.notEqual(route,saveCanvasSeed(owner,seed('另一份')));
  saveCanvasSeed(otherUser,seed('其他账号'));saveCanvasSeed(otherSpace,seed('其他空间'));
  assert.deepEqual(canvasDrafts(owner).map(draft=>draft.name),['本地资料整理','另一份']);
  assert.equal(canvasDrafts(owner)[0].href,'#'+route);
  const blankRoute=saveCanvasSeed(owner);
  const readDraft=route=>{
    const token=new URLSearchParams(route.split('?')[1]).get('draft');
    return JSON.parse(records.get('orchpact.canvas:alpha:studio:draft:new:'+token));
  };
  const blank=readDraft(blankRoute);
  assert.deepEqual(blank.value.operations.generate.execution_plan.nodes,[]);
  const original=structuredClone(blank.value);
  Object.assign(original,{id:'paper',name:'资料'});
  original.operations.generate.execution_plan={result_node:'step-1',nodes:[{key:'step-1',title:'审核',unit:'entry'}]};
  const fork=forkCanvasSeed(owner,original,'step-1','generate');
  const result=readDraft(fork),nodes=result.value.operations.generate.execution_plan.nodes;
  assert.match(fork,/operation=generate&node=step-1&branch=1$/);
  assert.notEqual(original.id,result.value.id);
  assert.equal(result.value.name,'资料 · 分支');
  assert.equal(nodes[0].title,'审核');
  assert.deepEqual(nodes[1].depends_on,['step-1']);
  assert.equal(result.selected,nodes[1].key);
  assert.equal(original.operations.generate.execution_plan.nodes.length,1);
  assert.ok(canvasDrafts(owner).some(draft=>draft.name==='资料 · 分支'));
  assert.throws(()=>forkCanvasSeed(owner,original,'absent','generate'),/找不到/);
});
