export function destination(admin) {
  const query = new URLSearchParams(location.search);
  const requested = query.get('return');
  const safe = requested?.startsWith('/') && !requested.startsWith('//') && !/[\\\x00-\x1f]/.test(requested);
  const url = new URL(safe ? requested : admin ? '/admin/' : '/workspace/', location.origin);
  if (['/account/', '/account'].includes(url.pathname) || !admin && ['/admin/', '/admin'].includes(url.pathname)) {
    url.pathname = admin ? '/admin/' : '/workspace/';
  }
  const workspace = query.get('workspace');
  if (workspace && !url.searchParams.has('workspace')) url.searchParams.set('workspace', workspace);
  if (!url.hash) url.hash = location.hash;
  const credit = query.get('credits');
  if (credit) url.hash = 'account?'+new URLSearchParams({credits:credit});
  else if (!safe && !url.hash && !admin) url.hash = 'account';
  return url.href;
}

export async function request(path, data) {
  const response = await fetch('/api/v1/auth/'+path, {
    credentials:'same-origin',
    ...(data ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)} : {}),
  });
  const result = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(result.detail) ? result.detail.map(item => item.msg).join('；') : result.detail;
    throw Object.assign(Error(detail || '请求失败'), {status:response.status});
  }
  return result;
}
