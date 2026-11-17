const test=require('node:test'),assert=require('node:assert/strict');
const Credits=require('../src/orchpact/static/account/credits.js');
const account=(available=100,user_id='alice')=>({user_id,admin:false,credits:{available,balance:available,held:0},credit_policy:{tokens_per_point:1000,search_points:10}});
function setup(value=account()){
 let me=value,reads=0;const c=new Credits(async()=>{reads++;return {ok:true,json:async()=>me};});
 c.update(value);return {c,set:v=>{me=v;},reads:()=>reads};
}
test('余额不足阻止新任务，补分后重新读取即可继续',async()=>{
 const s=setup(account(0));await assert.rejects(s.c.ensure(),e=>e.status===409&&e.code==='insufficient_credits');
 s.set(account(200));await s.c.ensure();assert.equal(s.c.me.credits.available,200);
 assert.match(s.c.notice(),/200/);assert.match(s.c.notice(),/1,000 Token/);assert.match(s.c.notice(),/10 积分/);
 s.set({...account(200),credit_policy:{tokens_per_point:500,search_points:20}});await s.c.refresh();assert.match(s.c.notice(),/500 Token/);assert.match(s.c.notice(),/20 积分/);
});
test('协作重试按付款人判断，查看者余额为零不阻止他人的任务',async()=>{
 const s=setup(account(0));await s.c.ensure({payer:'bob',retry:true});
 await assert.rejects(s.c.ensure({payer:'alice',retry:true}),/积分/);
 await s.c.ensure({retry:true}); // Old servers with no payer field defer to backend.
});
test('平台管理员无个人余额也可执行，缺少普通账号余额时不能按免费处理',async()=>{
 await setup({admin:true,user_id:null,credits:null}).c.ensure();
 await assert.rejects(setup({admin:false,user_id:'alice'}).c.ensure(),e=>e.code==='credits_unavailable');
});
test('会话换人后不能用原页面资料提交任务',async()=>{
 const s=setup();s.set(account(500,'bob'));await assert.rejects(s.c.ensure(),e=>e.code==='account_changed');
 assert.equal(s.c.me.user_id,'alice');
});
test('并发刷新共用请求，未知消耗与真实零消耗分开显示',async()=>{
 const s=setup();await Promise.all([s.c.refresh(),s.c.refresh()]);assert.equal(s.reads(),1);
 assert.match(s.c.describe(null),/尚未返回/);
 assert.match(s.c.describe({points:0,complete:true,model_tokens:0,search_requests:0}),/^0 积分/);
 assert.match(s.c.describe({points:1.5,complete:false,model_tokens:1500,search_requests:0,unmetered_model_requests:1}),/已知部分.*待核算/);
});
test('服务端已完成的原任务直接接收，不检查余额或再次重试',async()=>{
 const s=setup(account(0));let retries=0;
 const job=await s.c.retryInvocation('j',async()=>({id:'j',status:'completed'}),async()=>{retries++;});
 assert.equal(job.status,'completed');assert.equal(s.reads(),0);assert.equal(retries,0);
});
test('跨页重试冲突后重新接收原调用，真实余额拒绝仍保留错误',async()=>{
 const s=setup();let reads=0,retries=0;
 const job=await s.c.retryInvocation('j',async()=>++reads===1?{id:'j',status:'failed',payer_user_id:'alice',retryable_tasks:['t']}:{id:'j',status:'running'},async()=>{retries++;throw Object.assign(Error('已重试'),{status:409});});
 assert.equal(job.status,'running');assert.equal(retries,1);
 await assert.rejects(s.c.retryInvocation('j',async()=>({id:'j',status:'failed',payer_user_id:'bob',retryable_tasks:['t']}),async()=>{throw Object.assign(Error('积分不足'),{status:409});}),/积分不足/);
});
