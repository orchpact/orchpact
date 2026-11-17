(function(root,factory){const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.ApplicationCredits=value;})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const active=['queued','running','waiting','completed'];
 const number=value=>Number(value).toLocaleString('zh-CN',{maximumFractionDigits:6});
 return class ApplicationCredits {
  constructor(fetchImpl,onChange=()=>{}){this.fetch=fetchImpl;this.onChange=onChange;this.me=null;this.loading=null;}
  update(me){this.me=me;this.onChange(me);return me;}
  async refresh(){
   if(this.loading)return this.loading;
   this.loading=(async()=>{const response=await this.fetch('/api/v1/auth/me',{credentials:'same-origin'});const value=await response.json();if(!response.ok)throw Object.assign(Error(value.detail||'积分状态暂时无法读取'),{status:response.status});if(this.me&&(value.user_id!==this.me.user_id||value.admin!==this.me.admin))throw Object.assign(Error('账号已变更，请刷新页面后继续'),{status:409,code:'account_changed'});return this.update(value);})();
   try{return await this.loading;}finally{this.loading=null;}
  }
  async ensure({payer,retry=false}={}){
   const me=await this.refresh();if(me.admin)return me;
   // A shared-task retry can be funded by someone other than the viewer.
   if(retry&&(payer===undefined||payer!==me.user_id))return me;
   if(!me.credits||!Number.isFinite(me.credits.available))throw Object.assign(Error('积分状态暂时无法读取，请刷新后重试'),{status:503,code:'credits_unavailable'});
   if(me.credits.available<=0)throw Object.assign(Error('可用积分不足，请联系管理员补分后重试；已保存内容不会丢失'),{status:409,code:'insufficient_credits'});
   return me;
  }
  isCreditError(error){return error?.code==='insufficient_credits'||error?.code==='credits_unavailable'||/积分不足|积分账户不可用/.test(error?.message||'');}
  notice(){
   if(this.me?.admin)return '平台管理员发起的任务不使用个人积分；协作任务重试仍由原发起者支付。';
   const wallet=this.me?.credits,policy=this.me?.credit_policy;
   const balance=wallet?'可用 '+number(wallet.available)+' 积分，预留 '+number(wallet.held)+' 积分。':'积分状态尚未读取。';
   const rate=policy?'默认每 '+number(policy.tokens_per_point)+' Token 扣 1 积分，搜索每次 '+number(policy.search_points)+' 积分；提供者单独定价优先。':'';
   const specific=(this.me?.provider_prices||[]).filter(p=>p.pricing).map(p=>p.label+'：'+number(p.pricing.points)+'积分/'+(p.pricing.unit==='request'?'次':number(p.pricing.unit_size)+' Token')).join('；');
   return balance+rate+(specific?specific+'。':'')+'调用先预留、后结算；按Token计费且用量未知时继续预留。协作任务由原发起者支付，预留不足需补分后重试。';
  }
  describe(usage){
   if(!usage||!Number.isFinite(usage.points))return '本次用量尚未返回，不代表免费';
   return number(usage.points)+' 积分'+(usage.complete?'':'（已知部分）')+' · '+number(usage.model_tokens||0)+' Token · 搜索 '+number(usage.search_requests||0)+' 次'+(usage.unmetered_model_requests?' · '+usage.unmetered_model_requests+' 次调用待核算':'');
  }
  async retryInvocation(id,read,retry){
   const current=await read(id);if(active.includes(current.status))return current;
   const task=current.retryable_tasks?.[0];if(!task)throw Error('当前没有可重试任务，请刷新任务状态');
   await this.ensure({payer:current.payer_user_id,retry:true});
   try{await retry(task);}catch(error){if((error.status||error.httpStatus)!==409)throw error;const latest=await read(id);if(active.includes(latest.status))return latest;throw error;}
   return read(id);
  }
 };
});
