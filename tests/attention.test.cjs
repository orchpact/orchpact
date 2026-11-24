const test=require('node:test'),assert=require('node:assert/strict');
const {items,accountingLabel}=require('../src/orchpact/static/kernel/attention.js');
const project=extra=>({id:'one',application_id:'fixture',operation:'create',status:'running',requests:[],tasks:[],questions:[],...extra});

test('执行中调用和正常依赖等待不制造人工待办',()=>{
 const run=project({usage:{complete:false},status:'waiting',requests:[{id:'r',status:'started',accounting:{state:'held'}}],tasks:[{id:'work',status:'completed',review:'pending',artifact_id:'a'}]});
 assert.deepEqual(items({projects:[run]}),[]);
});
test('已核定费用但 Token 未知，不重复要求核定',()=>{
 const request={id:'r',status:'completed',credits:{status:'missing_usage'},accounting:{state:'settled',settled_points:2}};
 assert.deepEqual(items({projects:[project({requests:[request]})]}),[]);
 assert.equal(accountingLabel(request),'已结算 2 积分 · Token 用量未知');
});
test('停止请求的费用预留与未知执行结果分开归类',()=>{
 const requests=[{id:'fee',status:'completed',accounting:{state:'held'}},{id:'result',status:'unknown',accounting:{state:'settled'}}];
 const result=items({projects:[project({requests})]});
 assert.deepEqual(result.map(x=>[x.kind,x.reference]),[['receipt','result'],['accounting','fee']]);
 assert.match(result[0].action,/不要自动重发/);
});
test('只有当前、需要独立验收且未通过的产物进入待办',()=>{
 const tasks=[{id:'review',status:'completed',review:'pending',artifact_id:'a',requires_independent_review:true},{id:'accepted',status:'completed',review:'accepted',artifact_id:'b',requires_independent_review:true},{id:'retired',status:'completed',review:'pending',artifact_id:'c',requires_independent_review:true,required:false}];
 assert.deepEqual(items({projects:[project({tasks})]}).map(x=>x.reference),['review']);
});
test('仅待审批的外部写操作进入待办',()=>{
 const tool_approvals=[{id:'write',status:'pending'},{id:'old',status:'stale'},{id:'sent',status:'used'}];
 assert.deepEqual(items({projects:[project({tool_approvals})]}).map(x=>[x.kind,x.reference]),[['approval','write']]);
});
test('问题与异常保留项目上下文，分类不修改快照',()=>{
 const state={projects:[project({status:'partial',questions:[{id:'q'}]})]},before=JSON.stringify(state);
 const result=items(state);
 assert.deepEqual(result.map(x=>x.kind),['question','execution']);
 assert.ok(result.every(x=>x.project_id==='one'&&x.application_id==='fixture'));
 assert.equal(JSON.stringify(state),before);
});
test('未知金额保持未知，已确认的零积分可以显示零',()=>{
 assert.match(accountingLabel({status:'unknown',accounting:{state:'held',reserved_points:null}}),/金额待确认/);
 assert.equal(accountingLabel({accounting:{state:'settled',settled_points:0}}),'已结算 0 积分');
 assert.equal(accountingLabel({}),'无个人积分扣账记录');
});
test('已取消项目不再要求继续答复或验收，但保留账务与回执待办',()=>{
 const run=project({status:'cancelled',questions:[{id:'q'}],tasks:[{id:'review',status:'completed',review:'pending',artifact_id:'a',requires_independent_review:true}],requests:[{id:'r',status:'unknown',accounting:{state:'held'}}]});
 assert.deepEqual(items({projects:[run]}).map(x=>x.kind),['receipt']);
});
