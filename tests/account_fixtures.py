"""Generic account and application fixtures for isolated protocol tests."""
from orchpact.access import password_hash
from orchpact.db import ident, now


ADMIN_USERNAME = 'fixture-owner'
ADMIN_PASSWORD = 'fixture-owner-test-password'


def bootstrap_admin(service,client):
    actor = service.access.setup_admin(ADMIN_USERNAME,ADMIN_PASSWORD,offline=True)
    client.cookies.set('orchpact_session',service.access.session(actor.user_id))
    return actor


def create_user(service,client,name,password,hashed=None,audience=''):
    user=ident()
    with service.db.tx() as c:
        c.execute("INSERT INTO users(id,username,password,created) VALUES(%s,%s,%s,%s)",(user,name,hashed or password_hash(password),now()))
        service.access.new_workspace(c,user,'私人空间',True)
        service.access.wallets.create(c,user)
        service.access.wallets.entry(c,user,'adjustment',10000*1000000,reason='isolated test budget')
    client.cookies.set('orchpact_session',service.access.session(user,audience=audience))
    return user


def register_package(client,app='demo',operations=None,name='Fixture'):
    """Register a portable symbolic package through the public API."""
    operations=operations or {'run':dict(instruction='Execute',output_schema={'type':'object'},search_limit=9)}
    operations={key:dict({k:v for k,v in op.items() if k not in ('unit_id','execution_plan')},unit='entry') for key,op in operations.items()}
    manifest=dict(id=app,name=name,version='1.0.0',require_signature=False,units=[dict(key='entry',name='Entry',purpose='Execute',prompt='',parent=None)],operations=operations)
    response=client.post('/api/v1/organization-packages',json=manifest)
    response.raise_for_status()
    return response.json()


def synthetic_frontends(root,apps):
    """Small arithmetic consumers authored entirely inside this test suite."""
    import json
    result={}
    for index,app in enumerate(apps):
        directory=root/app/'dist';directory.mkdir(parents=True)
        (directory/'index.html').write_text('<!doctype html><html><head><link rel="icon" href="favicon.svg"><link rel="stylesheet" href="style.css"></head><body><h1>'+app+'</h1><script src="app.js"></script></body></html>')
        (directory/'favicon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20"/></svg>')
        for name in ('app.js','client.js','style.css'):(directory/name).write_text('/* Public protocol fixture. */')
        (directory/'integration.json').write_text(json.dumps(dict(id=app)))
        result[app]=dict(route='/consumer-'+str(index),directory=str(directory))
    return result
