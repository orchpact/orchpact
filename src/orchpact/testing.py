"""Isolated real PostgreSQL fixtures. Never uses ORCHPACT_DATABASE_URL or production data.

Use ORCHPACT_TEST_DATABASE_URL for a dedicated test server with CREATEDB permission,
otherwise start an ephemeral local cluster with installed PostgreSQL binaries.
Nothing runs until database_url_for() is explicitly called by a test or eval.
"""
import atexit
import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo

_lock=threading.Lock()
_cluster=None
_base=None
_databases={}
_session=uuid.uuid4().hex[:10]


def _bin():
    candidates=[os.environ.get('ORCHPACT_PG_BIN',''),str(Path(shutil.which('postgres') or '').parent)]
    candidates += ['/opt/homebrew/opt/postgresql@18/bin','/opt/homebrew/opt/postgresql@17/bin','/opt/homebrew/opt/postgresql@16/bin']
    for path in candidates:
        if path and all((Path(path)/name).is_file() for name in ('postgres','initdb','pg_ctl','pg_dump')):return Path(path)
    raise RuntimeError('测试需要真实 PostgreSQL：提供 ORCHPACT_TEST_DATABASE_URL，或安装 PostgreSQL 并设置 ORCHPACT_PG_BIN')


def _server():
    global _base,_cluster
    if _base:return _base
    if os.environ.get('ORCHPACT_TEST_DATABASE_URL'):
        _base=os.environ['ORCHPACT_TEST_DATABASE_URL'];return _base
    binary=_bin();folder=Path(tempfile.mkdtemp(prefix='orchpact-pg-tests-'))
    data=folder/'data';sockets=folder/'socket';sockets.mkdir(mode=0o700)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    try:
        subprocess.run([str(binary/'initdb'),'-D',str(data),'-U','orchpact_test_admin','--auth=trust','--encoding=UTF8','--locale=C'],check=True,capture_output=True)
        # Private Unix socket only. Durability remains enabled in tests.
        subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-l',str(folder/'server.log'),'-o',f"-k {sockets} -p {port} -c listen_addresses='' -c max_connections=100",'-w','start'],check=True,capture_output=True)
        _cluster=(binary,folder,data)
        _base=make_conninfo(host=str(sockets),port=str(port),user='orchpact_test_admin',dbname='postgres')
        os.environ.setdefault('ORCHPACT_PG_DUMP',str(binary/'pg_dump'))
        return _base
    except BaseException:shutil.rmtree(folder,ignore_errors=True);raise


def database_url_for(data_directory):
    """Create one disposable database per data directory, reusable across restarts."""
    path=Path(data_directory).resolve()
    with _lock:
        if path in _databases:return _databases[path]
        base=_server();name='orchpact_test_'+_session+'_'+hashlib.sha256(str(path).encode()).hexdigest()[:16]
        with psycopg.connect(base,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
        params=conninfo_to_dict(base);params['dbname']=name
        # Keep the public Settings boundary URI based; no passwords are printed.
        from urllib.parse import quote,urlencode
        user=quote(params.pop('user',''),safe='');password=params.pop('password',None)
        auth=user+(':'+quote(password,safe='') if password is not None else '')
        host=params.pop('host','localhost');port=params.pop('port',None);params.pop('dbname')
        if host.startswith('/'):
            params['host']=host;authority=auth+'@localhost'+(':'+str(port) if port else '')
        else:authority=auth+'@'+('['+host+']' if ':' in host else host)+(':'+str(port) if port else '')
        url='postgresql://'+authority+'/'+name+('?' + urlencode(params) if params else '')
        _databases[path]=url
        if path.name=='var':
            env=path.parent/'.env'
            env.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            lines=env.read_text().splitlines() if env.exists() else []
            if not any(line.startswith('ORCHPACT_DATABASE_URL=') for line in lines):
                env.write_text('\n'.join(lines+['ORCHPACT_DATABASE_URL='+url])+'\n');env.chmod(0o600)
        return url


def _cleanup():
    if _base:
        try:
            with psycopg.connect(_base,autocommit=True,connect_timeout=3) as c:
                for url in _databases.values():
                    name=conninfo_to_dict(url)['dbname']
                    c.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()',(name,))
                    c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        except psycopg.Error:pass
    if _cluster:
        binary,folder,data=_cluster
        result=subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],capture_output=True,timeout=30)
        if result.returncode==0:shutil.rmtree(folder,ignore_errors=True)

atexit.register(_cleanup)


def fixture_provider_credentials(service, credentials=None, *, default_model=None, search=None):
    """Attach explicit synthetic credentials to an isolated test instance's presets."""
    from .adapters.database import ident

    manager = service.configuration
    original_seed = manager.seed
    credentials = credentials or {}
    search = search or {}
    references = {}

    def reference(secret):
        if secret not in references:
            key = ident()
            manager.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            fd = os.open(manager.directory/key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as stream:
                stream.write(secret)
            references[secret] = key
        return references[secret]

    def seed():
        value = original_seed()
        for row in value['providers']:
            if row['category'] in ('text','image'):
                secret = credentials.get(row['id'],default_model)
            else:
                secret = search.get(row['id'])
            if secret:
                row['_secret_ref'] = reference(secret)
        return value

    manager.seed = seed
