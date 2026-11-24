(function(root,factory){const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else {root.OrchpactClient=value;}})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 return class OrchpactClient {
  constructor(fetchImpl=globalThis.fetch.bind(globalThis),workflowId=globalThis.document?.body?.dataset?.orchpactWorkflow||''){this.fetch=fetchImpl;this.workflow='workflows/'+encodeURIComponent(workflowId);}
  async call(path,method='GET',body,headers={}){
   const response=await this.fetch('/api/v1/'+path,{method,headers:{'Content-Type':'application/json',...headers},credentials:'same-origin',...(body===undefined?{}:{body:JSON.stringify(body)})});
   let value;try{value=await response.json();}catch{throw Error('服务暂时无法响应，请稍后重新连接');}
   if(!response.ok){const message=typeof value.detail==='string'?value.detail:response.status===422?'输入未通过结构校验':'请求失败';const error=Error(response.status===401?'会话已过期，请重新连接':message);error.status=response.status;error.httpStatus=response.status;error.code=value.code;throw error;}return value;
  }
  invoke(payload,key){return this.call(this.workflow+'/runs','POST',payload,{'Idempotency-Key':key});}
  invocation(id){return this.call('runs/'+encodeURIComponent(id));}
  invocations(){return this.call(this.workflow+'/runs');}
  async receipt(id){return (await this.call('requests/'+encodeURIComponent(id))).receipt;}
  cancel(project){return this.call('runs/'+encodeURIComponent(project)+'/cancel','POST',{});}
  retry(task){return this.call('work-items/'+encodeURIComponent(task)+'/retry','POST',{}, {'Idempotency-Key':globalThis.crypto.randomUUID()});}
  reply(question,content){return this.call('questions/'+encodeURIComponent(question)+'/answers','POST',{content});}
  ensureCredits(){return globalThis.WorkspaceContext?.credits?.ensure();}
  async retryInvocation(id){
   const credits=globalThis.WorkspaceContext?.credits;
   if(credits)return credits.retryInvocation(id,x=>this.invocation(x),task=>this.retry(task));
   const current=await this.invocation(id);if(['queued','running','waiting','completed'].includes(current.status))return current;
   if(!current.retryable_tasks?.length)throw Error('当前没有可重试任务');
   try{await this.retry(current.retryable_tasks[0]);}catch(error){if(error.status!==409)throw error;const latest=await this.invocation(id);if(['queued','running','waiting','completed'].includes(latest.status))return latest;throw error;}
   return this.invocation(id);
  }
  login(token){return this.call('session','POST',{token});}
 };
});
