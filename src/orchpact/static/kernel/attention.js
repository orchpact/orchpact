/* Read-only classification of the server-scoped recent-project snapshot. */
(function(root,factory){
 const api=factory();
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.OrchpactAttention=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
 'use strict';
 const labels={accounting:'费用待核定',receipt:'结果待确认',review:'待独立验收',question:'待答复',execution:'执行需处理'};
 function items(state){
  const result=[];
  for(const project of state.projects||[]){
   const add=(kind,reference,reason,action)=>result.push({kind,label:labels[kind],project_id:project.id,application_id:project.application_id,workflow_id:project.workflow?.id,operation:project.operation,reference,reason,action});
   for(const request of project.requests||[]){
    if(request.status==='unknown')add('receipt',request.id,'外部调用结果尚未确认；已核定费用也不代表获得了执行结果。','先核对真实回执；不要自动重发。');
    else if(request.status!=='started'&&request.accounting?.state==='held')add('accounting',request.id,'请求已停止，但积分仍在预留；Token 用量与费用核定分别记录。','查看回执，并由管理员在账号与积分中核定。');
   }
   for(const task of project.status==='cancelled'?[]:project.tasks||[]){
    if(task.required!==false&&task.requires_independent_review&&task.artifact_id&&task.review==='pending'&&['completed','partial'].includes(task.status))
     add('review',task.id,'当前产物需要独立验收，尚未取得通过记录。','打开项目，核验具体文件与哈希后提交决定。');
   }
   for(const question of project.status==='cancelled'?[]:project.questions||[])add('question',question.id,'项目有尚未回答的问题。','打开项目补充约束与依据。');
   if(['failed','interrupted','partial','rejected'].includes(project.status))
    add('execution',project.id,'项目未完整交付，保留了已有成果与执行记录。','先检查缺口、回执及授权，再决定调整、重试或结束。');
  }
  const order=['receipt','accounting','review','question','execution'];
  return result.sort((a,b)=>order.indexOf(a.kind)-order.indexOf(b.kind));
 }
 function accountingLabel(request){
  const value=request.accounting;
  if(!value||value.state==='untracked')return '无个人积分扣账记录';
  const amount=number=>typeof number==='number'&&Number.isFinite(number)?`${number} 积分`:'金额待确认';
  if(value.state==='settled')return `已结算 ${amount(value.settled_points)}${request.credits?.status==='missing_usage'?' · Token 用量未知':''}`;
  return `预留 ${amount(value.reserved_points)} · ${request.status==='started'?'调用进行中':'费用待核定'}`;
 }
 return {items,accountingLabel};
});
