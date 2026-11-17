"""PostgreSQL storage: explicit transactions, one database owner, native SQL."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict

from .schema import SCHEMA

OWNER_LOCK = int.from_bytes(b'ORCHOWNR','big')
WRITE_LOCK = int.from_bytes(b'ORCHWRIT','big')
FORMAT = 'orchpact-postgresql-v1'


def ident():return str(uuid.uuid4())
def now():return time.time()
def dumps(value):return json.dumps(value,ensure_ascii=False,allow_nan=False)
def loads(value):return json.loads(value)


class Row(dict):
    """Named domain records with ordinal access for scalar queries."""
    def __iter__(self):return iter(self.values())
    def __getitem__(self,key):
        return tuple(self.values())[key] if isinstance(key,int) else super().__getitem__(key)


def record_factory(cursor):
    names=[column.name for column in cursor.description] if cursor.description else []
    return lambda values:Row(zip(names,values))


class Connection(psycopg.Connection):
    def executemany(self,query,parameters):
        cursor=self.cursor();cursor.executemany(query,parameters);return cursor


class Database:
    def __init__(self,url,backup_directory=None):
        if not isinstance(url,str) or not url.startswith(('postgresql://','postgres://')):
            raise ValueError('请通过 ORCHPACT_DATABASE_URL 配置 PostgreSQL 连接，仅支持 PostgreSQL')
        try:conninfo_to_dict(url)
        except psycopg.ProgrammingError:raise ValueError('PostgreSQL 连接配置无效') from None
        self.url=url
        self.backup_directory=Path(backup_directory) if backup_directory is not None else None
        self.owner=None;self._owner_connection=None;self.owner_pid=None

    def connect(self,*,autocommit=False):
        try:
            return Connection.connect(self.url,autocommit=autocommit,row_factory=record_factory,
                connect_timeout=10,application_name='orchpact',
                options='-c search_path=public -c synchronous_commit=on -c lock_timeout=15000 -c statement_timeout=60000 -c idle_in_transaction_session_timeout=60000')
        except psycopg.OperationalError:
            raise RuntimeError('无法连接 PostgreSQL，请检查数据库服务与 ORCHPACT_DATABASE_URL') from None

    def acquire_owner(self):
        if self._owner_connection:return
        connection=self.connect(autocommit=True)
        try:
            if not connection.execute('SELECT pg_try_advisory_lock(%s)',(OWNER_LOCK,)).fetchone()[0]:
                raise RuntimeError('此 PostgreSQL 数据库已有 Orchpact 主进程，初始化未执行')
            self._owner_connection=connection
            self.owner_pid=connection.execute('SELECT pg_backend_pid()').fetchone()[0]
        except BaseException:connection.close();raise

    def close(self):
        if self._owner_connection:
            self._owner_connection.close();self._owner_connection=None;self.owner_pid=None

    def _owns_database(self,connection):
        if self.owner_pid is None:return False
        return connection.execute("""SELECT EXISTS(SELECT 1 FROM pg_locks
            WHERE locktype='advisory' AND pid=%s AND granted
            AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
            AND classid=%s AND objid=%s AND objsubid=1)""",
            (self.owner_pid,OWNER_LOCK>>32,OWNER_LOCK&0xffffffff)).fetchone()[0]

    def healthy(self):
        try:
            with self.connect() as c:return self._owns_database(c)
        except (RuntimeError,psycopg.Error):return False

    def verify_file_store(self):
        """Verify a stopped-instance backup without initializing another database."""
        if self.backup_directory is None:raise ValueError('缺少实例文件目录')
        marker=self.backup_directory.parent/'database-instance'
        with self.connect() as c:
            if not self.has_table(c,'instance_metadata'):
                raise ValueError('数据库尚未初始化为 Orchpact 实例')
            values=dict(c.execute('SELECT key,value FROM instance_metadata').fetchall())
        if values.get('format')!=FORMAT or not marker.is_file() or marker.read_text().strip()!=values.get('files_instance_id'):
            raise ValueError('PostgreSQL 与本地文件数据目录不匹配，请恢复配套备份')

    @staticmethod
    def has_table(connection,name):
        return connection.execute('SELECT to_regclass(%s)',('public.'+name,)).fetchone()[0] is not None

    def initialize(self,owner):
        if not owner.is_locked:raise RuntimeError('初始化必须先取得数据目录进程锁')
        self.owner=owner;self.acquire_owner()
        created_marker=None
        initialized=False
        try:
            with self.tx() as c:
                tables={row[0] for row in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
                if tables and ('instance_metadata' not in tables or not c.execute("SELECT 1 FROM instance_metadata WHERE key='format' AND value=%s",(FORMAT,)).fetchone()):
                    raise ValueError('目标数据库不是 Orchpact PostgreSQL 实例，请使用独立空库')
                for statement in SCHEMA:self._install(c,statement)
                c.execute("INSERT INTO instance_metadata VALUES('format',%s) ON CONFLICT DO NOTHING",(FORMAT,))
                self.mark_migration(c,'0001_initial_schema')
                if self.backup_directory is not None:
                    marker=self.backup_directory.parent/'database-instance'
                    binding=c.execute("SELECT value FROM instance_metadata WHERE key='files_instance_id'").fetchone()
                    if binding:
                        if not marker.is_file() or marker.read_text().strip()!=binding[0]:
                            raise ValueError('PostgreSQL 与本地文件数据目录不匹配，请恢复配套备份')
                    else:
                        if marker.exists():raise ValueError('本地数据目录已绑定其他数据库，请使用独立目录')
                        identity=ident();marker.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                        fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        with os.fdopen(fd,'w') as stream:
                            stream.write(identity+'\n');stream.flush();os.fsync(stream.fileno())
                        created_marker=marker
                        c.execute("INSERT INTO instance_metadata VALUES('files_instance_id',%s)",(identity,))
            initialized=True
        except BaseException as exc:
            if created_marker and not initialized and not isinstance(exc,psycopg.OperationalError):created_marker.unlink(missing_ok=True)
            self.close();raise

    @staticmethod
    def _install(connection,statement):
        statement=re.sub(r'^CREATE (TABLE|UNIQUE INDEX|INDEX) ',r'CREATE \1 IF NOT EXISTS ',statement,count=1)
        connection.execute(statement)

    @contextmanager
    def tx(self):
        # Preserve the single-writer command boundary: admission, balances, graph,
        # audit and state changes commit together. Reads use independent MVCC connections.
        with self.connect() as connection:
            with connection.transaction():
                connection.execute('SELECT pg_advisory_xact_lock(%s)',(WRITE_LOCK,))
                if not self._owns_database(connection):raise RuntimeError('PostgreSQL 实例所有权已失效，拒绝继续写入')
                yield connection

    @staticmethod
    def mark_migration(connection,name):
        if not re.fullmatch(r'\d{4}_[a-z0-9_]+',name):raise ValueError('迁移名称须为 0001_name')
        connection.execute('INSERT INTO schema_migrations VALUES(%s,%s) ON CONFLICT DO NOTHING',(name,now()))

    def backup(self,name,directory=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',name):raise ValueError('备份名称无效')
        folder=Path(directory) if directory is not None else self.backup_directory
        if folder is None:raise ValueError('必须指定数据库备份目录')
        binary=os.environ.get('ORCHPACT_PG_DUMP') or shutil.which('pg_dump')
        if not binary:raise RuntimeError('备份需要 pg_dump（版本不得低于服务端主版本）')
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        target=folder/(name+'.dump')
        if target.exists():raise ValueError('备份已存在，拒绝覆盖')
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        try:
            # Credentials stay out of command arguments, messages and logs.
            env=dict(os.environ)
            options={option.keyword.decode():option.envvar.decode() for option in psycopg.pq.Conninfo.get_defaults() if option.envvar}
            for key,value in conninfo_to_dict(self.url).items():
                if key not in options:raise ValueError('pg_dump 不支持此连接选项：'+key)
                env[options[key]]=value
            result=subprocess.run([binary,'--format=custom','--no-owner','--no-acl','--file',str(target)],env=env,capture_output=True,timeout=180)
            if result.returncode:raise RuntimeError('pg_dump 备份失败，请检查连接权限与客户端版本')
            with target.open('rb') as stream:os.fsync(stream.fileno())
            return target
        except BaseException:target.unlink(missing_ok=True);raise

    def migrate(self,name,statements):
        if not self.owner or not self.owner.is_locked or not self._owner_connection:raise RuntimeError('迁移需要实例独占锁')
        if not re.fullmatch(r'\d{4}_[a-z0-9_]+',name):raise ValueError('迁移名称须为 0001_name')
        with self.connect() as c:
            if c.execute('SELECT 1 FROM schema_migrations WHERE name=%s',(name,)).fetchone():return False
        backup_name=name
        if self.backup_directory is not None and (self.backup_directory/(name+'.dump')).exists():
            backup_name=name[:80]+'-'+ident().replace('-','')[:12]
        self.backup(backup_name)
        with self.tx() as c:
            for statement in [statements] if isinstance(statements,str) else statements:c.execute(statement)
            self.mark_migration(c,name)
        return True

    @staticmethod
    def event(connection,project,task,kind,**detail):
        connection.execute('INSERT INTO events(project_id,task_id,kind,detail,created) VALUES(%s,%s,%s,%s,%s)',(project,task,kind,dumps(detail),now()))
