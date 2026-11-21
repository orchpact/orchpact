"""Administrator-owned provider configuration. Secret values are write-only files."""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit
from .database import dumps, loads, ident, now
from ..credits import validate_policy, policy_snapshot
from ..image_providers import image_options
from ..search import ENDPOINTS, KEYS, profile_config

TOOL_FIELDS = {'tool_interface','tool_interface_version','implementation_version','tool_policy','tool_verifier'}


def tool_policy(value):
    if not isinstance(value, dict) or set(value) - {'workspace_ids','application_ids','require_credential','network','max_concurrency'}:
        raise ValueError('工具策略包含未知字段')
    result = {}
    for field in ('workspace_ids','application_ids'):
        rows = value.get(field, [])
        if not isinstance(rows, list) or len(rows) > 64 or any(not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', item) for item in rows) or len(rows) != len(set(rows)):
            raise ValueError('工具策略范围须为不重复的有界标识列表')
        result[field] = list(rows)
    require_credential = value.get('require_credential', False)
    if type(require_credential) is not bool:
        raise ValueError('工具凭据要求须为布尔值')
    result['require_credential'] = require_credential
    concurrency = value.get('max_concurrency', 4)
    if type(concurrency) is not int or not 1 <= concurrency <= 64:
        raise ValueError('工具并发须为1到64')
    result['max_concurrency'] = concurrency
    network = value.get('network', {})
    if not isinstance(network, dict) or set(network) - {'allowed_hosts','allowed_ports','allow_private'}:
        raise ValueError('工具网络策略包含未知字段')
    hosts = network.get('allowed_hosts', [])
    if not isinstance(hosts, list) or len(hosts) > 16 or any(not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', host) or host.startswith('.') or host.endswith('.') for host in hosts) or len(hosts) != len(set(hosts)):
        raise ValueError('工具目标主机白名单无效')
    ports = network.get('allowed_ports', [443])
    if not isinstance(ports, list) or not ports or len(ports) > 8 or any(type(port) is not int or not 1 <= port <= 65535 for port in ports) or len(ports) != len(set(ports)):
        raise ValueError('工具目标端口白名单无效')
    private = network.get('allow_private', False)
    if type(private) is not bool:
        raise ValueError('工具私有地址开关须为布尔值')
    result['network'] = dict(allowed_hosts=[host.lower() for host in hosts],allowed_ports=ports,allow_private=private)
    return result


def tool_configuration_digest(record):
    value = {key:record[key] for key in ('adapter','base_url','tool_interface','tool_interface_version','implementation_version','tool_policy')}
    if record.get('tool_verifier') is not None:value['tool_verifier']=record['tool_verifier']
    raw = json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    return sha256(raw).hexdigest()


def persisted(url):
    from .database import Database
    db=Database(url)
    with db.connect() as connection:
        if not db.has_table(connection,'provider_configuration'):return None
        row=connection.execute('SELECT value FROM provider_configuration WHERE id=1').fetchone()
    return loads(row[0]) if row else None


def price(value,category):
    if value is None:return None
    if not isinstance(value,dict) or set(value)-{'unit','points','unit_size'}:raise ValueError('价格仅支持unit、points、unit_size')
    unit=value.get('unit'); amount=value.get('points')
    if unit not in ('tokens','request') or (category in ('search','tool') and unit!='request'):raise ValueError('该类型不支持此计费单位')
    if isinstance(amount,bool) or not isinstance(amount,(int,float)) or not Decimal(str(amount)).is_finite() or not 0<=amount<=1_000_000_000 or Decimal(str(amount)).as_tuple().exponent < -6:raise ValueError('价格须为0到10亿、最多6位小数')
    size=value.get('unit_size',1000 if unit=='tokens' else 1)
    if type(size) is not int or not 1<=size<=1_000_000_000 or (unit=='request' and size!=1):raise ValueError('计量基数无效')
    return dict(unit=unit,points=amount,unit_size=size)


class ProviderConfiguration:

    def __init__(self, db, settings, initialize=True):
        self.db = db
        self.settings = settings
        self.directory = settings.data / 'provider-secrets'

    def seed(self):
        rows=[]
        path=self.settings.root/'config/models.toml'
        raw=tomllib.loads(path.read_text()).get('models',{}) if path.exists() else self.settings.profiles
        for key,original in raw.items():
            p=deepcopy(original);kind=p.get('kind')
            if kind not in ('text','image'):continue
            rows.append(dict(id=key,category=kind,label=p.get('label',key),enabled=p.get('enabled',True) is not False,
                adapter=p.get('provider','openai_compatible'),base_url=p.get('base_url',''),model=p.get('model',''),key_env=p.get('key_env',''),secondary_key_env='',
                priority=p.get('priority',100),search=bool(p.get('search') or 'search' in p.get('capabilities',[])),thinking_disabled=p.get('thinking_disabled',False),
                image_options=p.get('image_options',{}),pricing=dict(unit='request',unit_size=1,points=100) if kind=='image' else None))
        search=next((profile_config(p) for p in self.settings.profiles.values() if 'search' in p.get('capabilities',[])),dict(mode='aggregate',providers=[],max_results=5,fetch_top=0))
        search_path=self.settings.root/'config/search.toml'
        imported=tomllib.loads(search_path.read_text()) if search_path.exists() else {}
        source_order=imported.get('search',{}).get('sources',list(ENDPOINTS))
        for name in ENDPOINTS:
            active=next((p for p in search['providers'] if p['id']==name),{})
            source=imported.get('providers',{}).get(name,{})
            rows.append(dict(id='search:'+name,category='search',label={'you':'You.com','deepseek':'DeepSeek Search','tavily':'Tavily'}[name],enabled=name in source_order and source.get('enabled') is not False,adapter=name,base_url=ENDPOINTS[name],model='',key_env=source.get('key_env',active.get('key_env',KEYS[name])),secondary_key_env='',priority=source_order.index(name)*10 if name in source_order else 100,search=False,thinking_disabled=False,image_options={},pricing=None))
        rows.append(dict(next(p for p in rows if p['id']=='search:deepseek'),id='search:deepseek:fetch',label='DeepSeek Fetch',adapter='deepseek_fetch',base_url='https://api.deepseek.com/v1/tools/fetch'))
        return dict(credits=dict(self.settings.credits),execution=dict(workers=self.settings.workers,max_tasks_per_project=self.settings.max_tasks_per_project),
            search={k:deepcopy(search[k]) for k in ('mode','max_results','fetch_top')},providers=rows)

    def read(self,c=None):
        if c is None:
            with self.db.connect() as c:return self.read(c)
        row=c.execute('SELECT * FROM provider_configuration WHERE id=1').fetchone()
        if not row:return 0,self.seed()
        value=loads(row['value'])
        return row['revision'],value

    def secret(self,record,secondary=False):
        field='_secondary_ref' if secondary else '_secret_ref'
        ref=record.get(field)
        if ref:
            if not re.fullmatch(r'[a-f0-9-]{36}',ref):raise ValueError('凭据引用无效')
            path=self.directory/ref
            return path.read_text() if path.exists() else ''
        return os.environ.get(record.get('secondary_key_env' if secondary else 'key_env',''),'')

    def ready(self,p):
        if p['category'] == 'tool':
            endpoint = p['adapter'] == 'builtin' or bool(p['base_url'])
            credential = not p['tool_policy']['require_credential'] or bool(self.secret(p))
            return p['enabled'] and endpoint and credential
        return p['enabled'] and bool(self.secret(p)) and (p['adapter']!='volcengine' or bool(self.secret(p,True)))

    def public(self):
        revision,value=self.read();value=deepcopy(value)
        for p in value['providers']:
            p['secret_configured']=bool(self.secret(p));p['secondary_configured']=bool(self.secret(p,True));p['available']=self.ready(p)
            p.pop('_secret_ref',None);p.pop('_secondary_ref',None)
        return dict(revision=revision,value=value,restart_required=value['execution']!=dict(workers=self.settings.workers,max_tasks_per_project=self.settings.max_tasks_per_project))

    def record(self,key,c=None):
        _,value=self.read(c)
        return next((deepcopy(p) for p in value['providers'] if p['id']==key),None)

    def resolve_tool(self, requirement, binding, workspace_id, application_id):
        provider = self.record(binding['endpoint_ref'])
        if not provider or provider['category'] != 'tool' or not provider['enabled']:
            raise ValueError('工具提供者不存在或已停用')
        if provider['adapter'] == 'mcp':
            raise ValueError('MCP transport 尚未评审并锁定依赖，当前不可绑定')
        if provider['tool_interface'] != requirement['interface'] or provider['tool_interface_version'] != requirement['version']:
            raise ValueError('工具接口或接口版本不兼容')
        if provider['implementation_version'] != binding['implementation_version']:
            raise ValueError('工具实现版本不兼容')
        policy = tool_policy(provider['tool_policy'])
        provider['tool_policy'] = policy
        from ..kernel.verification import verifier_metadata
        provider['tool_verifier']=verifier_metadata(provider.get('tool_verifier'))
        if policy['workspace_ids'] and workspace_id not in policy['workspace_ids']:
            raise ValueError('工具提供者未授权此 Workspace')
        if policy['application_ids'] and application_id not in policy['application_ids']:
            raise ValueError('工具提供者未授权此应用')
        if policy['require_credential'] and not self.secret(provider):
            raise ValueError('工具提供者尚未配置凭据')
        safe = dict(interface=provider['tool_interface'],interface_version=provider['tool_interface_version'],
            type=provider['adapter'],endpoint_ref=provider['id'],implementation_version=provider['implementation_version'],
            configuration_digest=tool_configuration_digest(provider))
        if provider['tool_verifier'] is not None:safe['verifier']=deepcopy(provider['tool_verifier'])
        return dict(binding=safe,provider=provider)

    def defaults(self,c=None):return self.read(c)[1]['credits']

    def charge_policy(self,kind,model,c=None,defaults=None,provider=None):
        category='search' if kind=='search' else 'text'
        key=model
        if model.startswith('search:') and model.endswith(':search'):key=model[:-7]
        p=deepcopy(provider) if provider is not None else self.record(key,c)
        if self.read(c)[0] and p and not p['enabled']:raise ValueError('提供者已停用，未发出新调用：'+p['id'])
        policy=policy_snapshot(defaults if defaults is not None and self.read(c)[0]==0 else self.defaults(c))
        if kind == 'tool' and (not p or p.get('category') != 'tool' or p.get('pricing') is None):
            raise ValueError('工具按次积分价格未知，拒绝发起调用')
        if p:
            category=p['category'];rate=p.get('pricing')
            if rate is not None:policy.update(version=2,provider_id=p['id'],category=category,pricing=deepcopy(rate))
        return policy

    def profiles(self):
        revision,value=self.read()
        if not revision:return self.settings.profiles
        search=self.search_config(value)
        profiles={}
        for p in value['providers']:
            if p['category'] not in ('text','image') or not self.ready(p):continue
            caps=['text','json'] if p['category']=='text' else ['image']
            if p['category']=='image' and p['adapter']!='openai_compatible':caps.append('image_reference')
            if p['search'] and search['providers']:caps.append('search')
            item=dict(id=p['id'],label=p['label'],kind=p['category'],model=p['model'],base_url=p['base_url'],key_env=p['key_env'],provider=p['adapter'],priority=p['priority'],capabilities=caps,json_mode=True,
                      thinking_disabled=p['thinking_disabled'],image_options=p['image_options'],credential=p)
            if 'search' in caps:item['search_config']=search
            profiles[p['id']]=item
        return profiles

    def search_config(self,value=None):
        value=value or self.read()[1];providers=[];fetch=None
        for p in sorted(value['providers'],key=lambda p:p['priority']):
            if p['category']!='search' or not self.ready(p):continue
            item=dict(id=p['adapter'],key_env=p['key_env'],credential=deepcopy(p),base_url=p['base_url'])
            if p['adapter']=='deepseek_fetch':fetch=item
            else:providers.append(item)
        return dict(value['search'],providers=providers,fetch_provider=fetch)

    def validate(self,value,old):
        if not isinstance(value,dict) or set(value)!={'credits','execution','search','providers'}:raise ValueError('配置结构不完整或包含未知字段')
        value=deepcopy(value);value['credits']=validate_policy(value['credits'])
        execution=value['execution']
        if not isinstance(execution,dict) or set(execution)!={'workers','max_tasks_per_project'} or any(type(n) is not int for n in execution.values()) or not 1<=execution['workers']<=64 or not 1<=execution['max_tasks_per_project']<=execution['workers']:raise ValueError('执行并发须为1到64，单项目并发须在1到总并发之间')
        search=value['search']
        if not isinstance(search,dict) or set(search)!={'mode','max_results','fetch_top'} or search['mode'] not in ('aggregate','fallback') or type(search['max_results']) is not int or not 1<=search['max_results']<=10 or type(search['fetch_top']) is not int or not 0<=search['fetch_top']<=3:raise ValueError('搜索策略无效')
        rows=value['providers']
        if not isinstance(rows,list) or not 1<=len(rows)<=100:raise ValueError('提供者须为1到100项')
        old_by_id={p['id']:p for p in old['providers']};ids=set();secrets=[]
        fields={'id','category','label','enabled','adapter','base_url','model','key_env','secondary_key_env','priority','search','thinking_disabled','image_options','pricing'}
        allowed={'text':{'openai_compatible'},'image':{'openai_compatible','openai','seedream','gemini'},'search':set(ENDPOINTS)|{'deepseek_fetch'},'tool':{'http','mcp','builtin'}}
        for index,p in enumerate(rows):
            if not isinstance(p,dict) or set(p)-fields-TOOL_FIELDS-{'secret','secondary_secret','secret_configured','secondary_configured','available'}:raise ValueError('提供者包含未知字段')
            for field in ('secret_configured','secondary_configured','available'):p.pop(field,None)
            category=p.get('category')
            if category=='tool':p.setdefault('tool_verifier',None)
            required=fields|TOOL_FIELDS if category=='tool' else fields
            if set(p)-{'secret','secondary_secret'}!=required:raise ValueError('提供者字段不完整')
            key=p['id'];category=p['category']
            if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_:-]{0,79}',key) or key in ids:raise ValueError('提供者ID无效或重复')
            ids.add(key)
            if not isinstance(category,str) or category not in allowed or not isinstance(p['adapter'],str) or p['adapter'] not in allowed[category]:raise ValueError('提供者类型或协议不支持')
            if key in old_by_id and (old_by_id[key]['category']!=category or old_by_id[key]['adapter']!=p['adapter']):raise ValueError('已有提供者类型/协议不可修改，请使用新ID')
            if category=='search' and key!='search:'+p['adapter'].replace('_fetch',':fetch'):raise ValueError('搜索渠道ID须与适配器对应')
            if any(type(p[f]) is not bool for f in ('enabled','search','thinking_disabled')):raise ValueError('开关须为布尔值')
            if type(p['priority']) is not int or not 0<=p['priority']<=10000:raise ValueError('优先级须为0到10000')
            if not isinstance(p['label'],str) or not p['label'].strip() or len(p['label'])>120:raise ValueError('名称须为1到120字符')
            if not isinstance(p['model'],str) or len(p['model'])>200 or (category in ('text','image') and not p['model'].strip()):raise ValueError('需要有效模型名称')
            for f in ('key_env','secondary_key_env'):
                if not isinstance(p[f],str) or (p[f] and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',p[f])):raise ValueError('凭据环境变量名无效')
            if category=='tool':
                from ..kernel.tools import validate_version
                from ..kernel.verification import verifier_metadata
                if not isinstance(p['tool_interface'],str) or not re.fullmatch(r'[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+',p['tool_interface']) or len(p['tool_interface'])>120:raise ValueError('工具提供者接口无效')
                validate_version(p['tool_interface_version']);validate_version(p['implementation_version'])
                p['tool_policy']=tool_policy(p['tool_policy'])
                p['tool_verifier']=verifier_metadata(p['tool_verifier'])
                if p['model'] or p['search'] or p['thinking_disabled'] or p['key_env'] or p['secondary_key_env'] or 'secondary_secret' in p:raise ValueError('工具提供者不支持模型字段或环境凭据')
            if category=='tool' and p['adapter']=='builtin':
                if p['base_url']!='':raise ValueError('builtin 工具地址须为空')
            else:
                try:
                    u=urlsplit(p['base_url']);u.port
                    if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:raise ValueError()
                except (ValueError,TypeError,AttributeError):raise ValueError('提供者地址须为无凭据、无查询参数的HTTPS URL') from None
            if category=='image':image_options(dict(provider=p['adapter'],image_options=p['image_options']))
            elif p['image_options']!={}:raise ValueError('只有图片类可配置图片选项')
            p['pricing']=price(p['pricing'],category)
            previous=old_by_id.get(key,{})
            for field,ref in (('secret','_secret_ref'),('secondary_secret','_secondary_ref')):
                raw=p.pop(field,None)
                if raw is not None:
                    if not isinstance(raw,str) or not 1<=len(raw)<=4096 or any(c in raw for c in '\r\n\x00'):raise ValueError('密钥须为1到4096字符的单行文本；留空请省略字段')
                    token=ident();p[ref]=token;secrets.append((token,raw))
                elif previous.get(ref):p[ref]=previous[ref]
            rows[index]=p
        # Disable rather than remove: existing organizations and in-flight jobs keep stable IDs.
        if set(old_by_id)-ids:raise ValueError('已有提供者不可删除，请关闭启用开关')
        if not any(p['category']=='text' and p['enabled'] and (p.get('_secret_ref') or os.environ.get(p['key_env'])) for p in rows):raise ValueError('至少保留一个已配置凭据的文本提供者')
        return value,secrets

    def save(self,revision,value,actor):
        created=[]
        try:
            with self.db.tx() as c:
                current,old=self.read(c)
                if type(revision) is not int or revision!=current:raise ValueError('配置已被其他管理员修改，请重新加载后保存')
                value,secrets=self.validate(value,old)
                if secrets:
                    self.directory.mkdir(parents=True,exist_ok=True,mode=0o700);self.directory.chmod(0o700)
                    for token,raw in secrets:
                        fd=os.open(self.directory/token,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        with os.fdopen(fd,'w') as stream:stream.write(raw)
                        created.append(token)
                c.execute('INSERT INTO provider_configuration VALUES(1,%s,%s) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,value=excluded.value',(current+1,dumps(value)))
                old_by_id={p['id']:p for p in old['providers']}
                changes=[dict(id=p['id'],before=old_by_id.get(p['id'],{}).get('pricing'),after=p['pricing']) for p in value['providers'] if p['pricing']!=old_by_id.get(p['id'],{}).get('pricing')]
                c.execute('INSERT INTO configuration_audit(revision,actor,summary,created) VALUES(%s,%s,%s,%s)',(current+1,actor,dumps({'provider_ids':[p['id'] for p in value['providers']],'pricing_changes':changes,'credentials_updated':len(secrets),'defaults_before':old['credits'],'defaults_after':value['credits']}),now()))
        except Exception:
            for token in created:(self.directory/token).unlink(missing_ok=True)
            raise
        return self.public()

    def history(self):
        with self.db.connect() as c:return [dict(r) for r in c.execute('SELECT revision,actor,summary,created FROM configuration_audit ORDER BY id DESC LIMIT 100')]
