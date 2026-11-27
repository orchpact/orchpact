from dataclasses import dataclass, field
from pathlib import Path
import os
import tomllib
from urllib.parse import urlparse
from dotenv import dotenv_values
from .credits import DEFAULT_POLICY, validate_policy

@dataclass(frozen=True)
class Settings:
    root: Path
    profiles: dict
    data: Path
    host: str = "127.0.0.1"
    port: int = 8780
    local_access: bool = True

    frontends: dict = field(default_factory=dict)

    public_origin: str = ""

    workers: int = 10
    max_tasks_per_project: int = 1
    database_url: str = field(default="",repr=False)
    products: dict = field(default_factory=dict)
    credits: dict = field(default_factory=lambda: dict(DEFAULT_POLICY))
    allow_experimental_planning: bool = False
    _configuration: object = field(default=None,repr=False,compare=False)

    def __post_init__(self):
        object.__setattr__(self,'credits',validate_policy(self.credits))
        if not self.database_url:
            object.__setattr__(self,'database_url',os.environ.get('ORCHPACT_DATABASE_URL',''))
        if not self.database_url.startswith(('postgresql://','postgres://')):
            raise ValueError('请配置 ORCHPACT_DATABASE_URL，Orchpact 使用 PostgreSQL')
        if not isinstance(self.public_origin,str):raise ValueError("public_origin 必须为字符串")
        if self.public_origin:
            u=urlparse(self.public_origin)
            try:u.port
            except ValueError:raise ValueError("public_origin 端口无效") from None
            if u.scheme!="https" or not u.hostname or u.username or u.password or u.query or u.fragment or u.path:
                raise ValueError("public_origin 必须是无路径的HTTPS域名，例如https://example.com")
            object.__setattr__(self,"local_access",False)
        if not isinstance(self.products,dict):raise ValueError("products 必须是应用到HTTPS域名的映射")
        if self.products or self.frontends:
            raise ValueError('子应用域名与前端托管已移除；请移出 products/frontends 配置，使用工作流直接执行')
        if type(self.workers) is not int or not 1 <= self.workers <= 64:
            raise ValueError("execution.workers 必须为1到64的整数")
        if type(self.max_tasks_per_project) is not int or not 1 <= self.max_tasks_per_project <= self.workers:
            raise ValueError("execution.max_tasks_per_project 必须为1到workers的整数")
        if type(self.allow_experimental_planning) is not bool:
            raise ValueError("execution.allow_experimental_planning 必须为布尔值")

    def origin_for_host(self,host):
        if self.public_origin and urlparse(self.public_origin).hostname==host:return self.public_origin
        return None

    @classmethod
    def load(cls, root, host="127.0.0.1", port=8780):
        root = Path(root).resolve()
        private=dotenv_values(root/".env",interpolate=False)
        database_url=os.environ.get("ORCHPACT_DATABASE_URL") or private.get("ORCHPACT_DATABASE_URL","")
        from .provider_config import persisted
        managed=persisted(database_url)
        # An empty catalog is valid for configuration; execution rejects missing capabilities.
        frontend_file=root/'config/frontends.toml'
        frontends=tomllib.loads(frontend_file.read_text()).get('frontends',{}) if frontend_file.exists() else {}
        execution_file=root/"config/execution.toml"
        execution=tomllib.loads(execution_file.read_text()).get("execution",{}) if execution_file.exists() else {}
        unknown=set(execution)-{"workers","max_tasks_per_project","allow_experimental_planning"}
        if unknown:raise ValueError("未知执行配置："+", ".join(sorted(unknown)))
        security_file=root/"config/security.toml"
        security=tomllib.loads(security_file.read_text()).get("security",{}) if security_file.exists() else {}
        if set(security)-{"public_origin","products"}:raise ValueError("未知security配置")
        credits_file=root/'config/credits.toml'
        credit_config=tomllib.loads(credits_file.read_text()) if credits_file.exists() else {}
        if set(credit_config)-{'credits'}:raise ValueError('积分配置只允许[credits]配置段')
        credits=credit_config.get('credits',{})
        if managed:execution={**execution, **managed['execution']}
        settings=cls(root, {}, root / "var", host, port, host in ("127.0.0.1", "localhost", "::1"),frontends,**execution,**security,credits=credits,database_url=database_url)
        if managed:
            from .provider_config import ProviderConfiguration
            from .db import Database
            db=Database(database_url,root/'var/backups')
            object.__setattr__(settings,'_configuration',ProviderConfiguration(db,settings,initialize=False))
        return settings

    def choose(self, capabilities, preferred=None):
        from .kernel.models import model_capabilities
        required = set(model_capabilities(capabilities))
        choices = [p for p in self.active_profiles().values() if required <= set(p["capabilities"])]
        if preferred:
            choices = [p for p in choices if p["id"] == preferred]
        if not choices:
            raise ValueError("没有可用模型支持所需能力：" + ", ".join(sorted(required)))
        return min(choices, key=lambda p: (p.get("priority", 100), p["id"]))

    def catalog(self):
        return [{k: p[k] for k in ("id", "label", "kind", "model", "capabilities", "priority", "provider") if k in p}
                for p in self.active_profiles().values()]

    def active_profiles(self):
        manager=getattr(self,'_configuration',None)
        return manager.profiles() if manager else {}

    def image_catalog(self):
        manager=getattr(self,'_configuration',None)
        if manager:
            return [dict(id=p['id'],label=p['label'],model=p['model'],provider=p['adapter'],priority=p['priority'],status='enabled' if p['available'] else 'disabled' if not p['enabled'] else 'missing_credentials') for p in manager.public()['value']['providers'] if p['category']=='image']
        path=self.root/'config/models.toml'
        profiles=tomllib.loads(path.read_text()).get('models',{}) if path.exists() else self.profiles
        return [dict(id=key,label=p.get('label',key),model=p.get('model',''),provider=p.get('provider','openai_compatible'),priority=p.get('priority',100),
            status='disabled' if p.get('enabled',True) is False else 'missing_credentials')
            for key,p in profiles.items() if p.get('kind')=='image']
