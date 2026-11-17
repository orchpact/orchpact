'use strict';
const form=document.querySelector('#admin-login'),message=document.querySelector('#login-error');
form.onsubmit=async event=>{
 event.preventDefault();const button=form.querySelector('button');button.disabled=true;message.textContent='';
 try{
  const response=await fetch('/api/v1/session',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:form.elements.token.value})});
  const value=await response.json();if(!response.ok)throw Error(value.detail||'验证失败');
  form.reset();
  if(location.pathname==='/admin/')location.reload();
  else location.replace('/admin/'+location.search+location.hash);
 }catch(error){message.textContent=error.message;}
 finally{button.disabled=false;}
};
