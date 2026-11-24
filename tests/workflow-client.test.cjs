const test=require('node:test'),assert=require('node:assert/strict');
const Client=require('../src/orchpact/static/orchpact-client.js');

test('直接调用工作流，不创建应用或签名身份',async()=>{
 const calls=[];
 const client=new Client(async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>({id:'run-1'})};},'report_one');
 assert.deepEqual(await client.invoke({operation:'generate',input:{}},'stable-key'),{id:'run-1'});
 assert.equal(calls[0].path,'/api/v1/workflows/report_one/runs');
 assert.equal(calls[0].options.headers['Idempotency-Key'],'stable-key');
 assert.equal(calls[0].options.credentials,'same-origin');
 assert.ok(!Object.keys(calls[0].options.headers).some(key=>key.toLowerCase().startsWith('x-orchpact-')));
 assert.equal(typeof client.saveDocument,'undefined');
});
test('按执行 ID 查询历史，不依赖当前安装修订',async()=>{
 const paths=[];const client=new Client(async path=>{paths.push(path);return {ok:true,json:async()=>({status:'completed'})};},'report');
 await client.invocation('run/one');await client.invocations();
 assert.deepEqual(paths,['/api/v1/runs/run%2Fone','/api/v1/workflows/report/runs']);
});
test('安装缺失和预算拒绝保留服务端错误，不自动重放调用',async()=>{
 let calls=0;const client=new Client(async()=>{calls++;return {ok:false,status:409,json:async()=>({code:'workflow_not_installed',detail:'先安装资源'})};},'report');
 await assert.rejects(client.invoke({operation:'generate',input:{}},'one'),error=>error.code==='workflow_not_installed'&&error.status===409);
 assert.equal(calls,1);
});
