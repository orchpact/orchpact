"""Own an instance before constructing writable services."""
from dataclasses import replace
from importlib import import_module
import os
from filelock import FileLock, Timeout

from .adapters.database import Database, ident, now, dumps
from .adapters.identity import Access
from .adapters.artifacts import Artifacts
from .provider_config import ProviderConfiguration
from .audit import Audit
from .services.platform import Platform
from .services.runner import Runner


class Instance:
    def __init__(self, settings, executor=None):
        self.settings = settings
        self.lock = FileLock(str(settings.data / 'process.lock'), thread_local=False)
        self.executor = executor
        self.service = None
        self.runner = None

    def open(self):
        self.settings.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.lock.acquire(timeout=0)
        except Timeout:
            raise RuntimeError('该实例已有主进程；初始化和迁移均未执行') from None
        try:
            db = Database(self.settings.database_url,self.settings.data/'backups')
            self.database=db
            db.initialize(self.lock)
            from .adapters.schema import MIGRATIONS
            for name, statements in MIGRATIONS:
                db.migrate(name, statements)
            # Upstream import initializes an app-data directory even with memory
            # disabled. Own that directory explicitly; never require a user home.
            storage = (self.settings.data / 'crewai-runtime').resolve()
            storage.mkdir(mode=0o700, exist_ok=True)
            os.environ['CREWAI_STORAGE_DIR'] = str(storage)
            import_module('.adapters.crewai', __package__)
            configuration = ProviderConfiguration(db, self.settings)
            revision, value = configuration.read()
            settings = replace(self.settings, **value['execution']) if revision else self.settings
            configuration.settings = settings
            object.__setattr__(settings, '_configuration', configuration)
            access = Access(db)
            access.audit = Audit(db)
            with db.tx() as c:
                row = c.execute("SELECT id FROM users WHERE username='platform-admin'").fetchone()
                if not row:
                    user = ident()
                    c.execute('INSERT INTO users(id,username,password,created) VALUES(%s,%s,%s,%s)', (user, 'platform-admin', '', now()))
                    workspace = access.new_workspace(c, user, '平台工作空间', True)
                    c.execute('INSERT INTO credit_wallets(user_id) VALUES(%s)', (user,))
                    c.execute("INSERT INTO instance_metadata VALUES('admin_user',%s)", (user,))
                    c.execute("INSERT INTO instance_metadata VALUES('admin_workspace',%s)", (workspace,))
                self.admin_user = c.execute("SELECT value FROM instance_metadata WHERE key='admin_user'").fetchone()[0]
                self.admin_workspace = c.execute("SELECT value FROM instance_metadata WHERE key='admin_workspace'").fetchone()[0]
            self.service = Platform(db, settings, Artifacts(settings.data / 'artifacts'), access, configuration)
            self.runner = Runner(self.service, self.executor)
            return self
        except BaseException:
            if getattr(self,'database',None):self.database.close()
            self.lock.release()
            raise

    def close(self):
        if self.runner:
            self.runner.stop()
        if getattr(self,'database',None):self.database.close()
        self.lock.release()
