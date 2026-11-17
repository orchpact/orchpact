"""Search wire protocols and evidence normalization; credentials stay server-side."""
import ipaddress
import os
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .provider_http import bounded_json

ENDPOINTS = {
    'deepseek': 'https://api.deepseek.com/v1/tools/search',
    'you': 'https://ydc-index.io/v1/search',
    'tavily': 'https://api.tavily.com/search',
}
KEYS = {'deepseek': 'DEEPSEEK_API_KEY', 'you': 'YDC_API_KEY', 'tavily': 'TAVILY_API_KEY'}


class SearchQuotaExceeded(ValueError):
    """A single project's search source has no request slots left."""


def quota_source(model):
    if model.startswith('search:'):
        source = model.split(':')[1]
        if source not in ENDPOINTS:raise ValueError('未知搜索来源：'+source)
        return source
    raise ValueError('搜索请求缺少来源标识')


def source_usage(connection, project):
    used = dict.fromkeys(ENDPOINTS, 0)
    for model, count in connection.execute(
            "SELECT model_id,COUNT(*) FROM requests WHERE project_id=%s AND kind='search' GROUP BY model_id", (project,)):
        used[quota_source(model)] += count
    return used


def configuration(value):
    if not isinstance(value, dict):raise ValueError('搜索配置须为表')
    if set(value) - {'search', 'providers'}:raise ValueError('搜索配置只允许search和providers')
    options = value.get('search', {})
    if not isinstance(options, dict):raise ValueError('search须为配置表')
    if set(options) - {'mode', 'sources', 'max_results', 'fetch_top'}:raise ValueError('未知search配置')
    mode = options.get('mode', 'aggregate')
    names = options.get('sources', list(ENDPOINTS))
    count = options.get('max_results', 5)
    fetch = options.get('fetch_top', 0)
    if mode not in ('aggregate', 'fallback'):raise ValueError('search.mode须为aggregate或fallback')
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or n not in ENDPOINTS for n in names) or len(set(names)) != len(names):
        raise ValueError('search.sources须为不重复的deepseek/you/tavily列表')
    if type(count) is not int or not 1 <= count <= 10:raise ValueError('search.max_results须为1到10')
    if type(fetch) is not int or not 0 <= fetch <= 3:raise ValueError('search.fetch_top须为0到3')
    providers = value.get('providers', {})
    if not isinstance(providers, dict):raise ValueError('providers须为配置表')
    if set(providers) - set(ENDPOINTS):raise ValueError('未知搜索供应商')
    for name,p in providers.items():
        if not isinstance(p,dict) or set(p) - {'enabled','key_env'}:raise ValueError('未知搜索供应商配置：'+name)
    active = []
    for name in names:
        p = providers.get(name, {})
        if set(p) - {'enabled', 'key_env'}:raise ValueError('未知搜索供应商配置：'+name)
        enabled = p.get('enabled', 'auto')
        key = p.get('key_env', KEYS[name])
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):raise ValueError('搜索key_env须为环境变量名')
        if type(enabled) is not bool and enabled != 'auto':raise ValueError('搜索enabled须为true/false或auto')
        if enabled is False:continue
        if not os.environ.get(key):
            if enabled is True:raise ValueError('搜索供应商 '+name+' 缺少环境变量 '+key)
            continue
        active.append(dict(id=name, key_env=key))
    return dict(mode=mode, providers=active, max_results=count, fetch_top=fetch)


def profile_config(profile):
    # A text profile may use Tavily directly or an explicit multi-source configuration.
    return profile.get('search_config') or dict(mode='aggregate', providers=[dict(id='tavily', key_env=profile.get('search_key_env', 'TAVILY_API_KEY'))], max_results=5, fetch_top=0)


def public_url(value):
    if not isinstance(value, str) or len(value) > 8192:return None
    try:
        u = urlsplit(value)
        if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password:return None
        u.port
        host = u.hostname.lower()
        if host in ('localhost',) or host.endswith(('.localhost', '.local', '.internal')):return None
        try:
            if not ipaddress.ip_address(host).is_global:return None
        except ValueError:pass
        return u
    except ValueError:return None


def canonical_url(value):
    u = public_url(value)
    if not u:return None
    host = u.hostname.lower()
    if ':' in host:host = '['+host+']'
    if u.port and u.port != (443 if u.scheme == 'https' else 80):host += ':'+str(u.port)
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid')]
    return urlunsplit((u.scheme.lower(), host, u.path or '/', urlencode(query), ''))


def search_request(client, provider, key, query, count):
    name = provider['id']
    if name == 'deepseek':body = dict(query=query, count=count, need_content=True)
    elif name == 'you':body = dict(query=query, count=count, extraction={'extraction_mode': 'highlights'})
    else:body = dict(query=query, search_depth='basic', max_results=count, include_raw_content='text', include_answer=False, include_usage=True)
    headers = {'X-API-Key': key} if name == 'you' else {'Authorization': 'Bearer '+key}
    response = client.post(provider.get('base_url',ENDPOINTS[name]), headers=headers, json=body)
    response.raise_for_status()
    data = bounded_json(response)
    if not isinstance(data, dict):raise ValueError('搜索响应须为对象')
    return data


def normalize(name, data, count):
    rows = data.get('results')
    if name == 'you':
        if not isinstance(rows, dict):raise ValueError('You搜索响应缺少results')
        sections = [rows.get('web', []), rows.get('news', [])]
        if any(not isinstance(s, list) for s in sections):raise ValueError('You搜索结果不是数组')
        rows = [r for group in zip_longest_rows(*sections) for r in group]
    if not isinstance(rows, list):raise ValueError('搜索响应缺少results数组')
    result = []
    for r in rows:
        if not isinstance(r, dict) or not canonical_url(r.get('url')):continue
        content = r.get('contents') or {}
        if not isinstance(content, dict):content = {}
        highlights = content.get('highlights') or r.get('snippets') or []
        if not isinstance(highlights, list):highlights = []
        excerpt = (r.get('raw_content') or r.get('content') or r.get('summary') or '\n'.join(h for h in highlights if isinstance(h, str)) or r.get('description') or r.get('snippet') or '')
        if not isinstance(excerpt, str):raise ValueError('搜索正文不是文本')
        date = r.get('date') or r.get('published_date') or r.get('page_age')
        result.append(dict(url=r['url'], title=str(r.get('title') or '')[:500], published_date=date if isinstance(date, str) else None,
                           excerpt=excerpt[:6000], provider=name, authority=r.get('authority') if name == 'deepseek' else None))
        if len(result) >= count:break
    return result


def zip_longest_rows(*groups):
    for index in range(max(map(len, groups), default=0)):
        yield [g[index] for g in groups if index < len(g)]


def merge(groups):
    # Round-robin avoids filling the entire context with only the first engine.
    sources = {}; ordered = []
    for row in zip_longest_rows(*groups):
        for result in row:
            key = canonical_url(result['url'])
            if key not in sources:
                source = dict(result, providers=[], retrievals=[])
                sources[key] = source; ordered.append(source)
            source = sources[key]
            if result['provider'] not in source['providers']:source['providers'].append(result['provider'])
            source['retrievals'].append(result)
    return ordered
