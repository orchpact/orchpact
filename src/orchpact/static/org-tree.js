(function(root,factory){const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.OrganizationTree=value;})(typeof globalThis!=='undefined'?globalThis:this,function(){
'use strict';
function build(units,query='',expanded=[]){
 const byId=new Map(units.map(u=>[u.id,u])),children=new Map(),open=new Set(expanded),term=query.trim().toLocaleLowerCase();
 for(const u of units){const parent=byId.has(u.parent_id)?u.parent_id:null;if(!children.has(parent))children.set(parent,[]);children.get(parent).push(u);}
 const matches=new Set(units.filter(u=>!term||(u.name+' '+u.purpose).toLocaleLowerCase().includes(term)).map(u=>u.id)),visible=new Set();
 for(const id of matches){let next=id;const visited=new Set();while(next&&byId.has(next)&&!visited.has(next)){visited.add(next);visible.add(next);next=byId.get(next).parent_id;}}
 function descendants(id,seen=new Set()){if(seen.has(id))return 0;seen=new Set([...seen,id]);return (children.get(id)||[]).reduce((n,u)=>n+1+descendants(u.id,seen),0);}
 function walk(parent,seen=new Set()){return (children.get(parent)||[]).filter(u=>!seen.has(u.id)&&(!term||visible.has(u.id))).map(u=>{const branch=(children.get(u.id)||[]).length>0,isOpen=branch&&(term?true:open.has(u.id));return {unit:u,matched:!!term&&matches.has(u.id),descendants:descendants(u.id),expanded:!!isOpen,branch,children:isOpen?walk(u.id,new Set([...seen,u.id])):[]};});}
 return {nodes:walk(null),total:units.length,roots:(children.get(null)||[]).length,matchCount:matches.size,searching:!!term};
}
return {build};
});
