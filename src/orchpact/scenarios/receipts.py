"""Resolve example verifier inputs through this candidate's frozen plan and accepted deliveries."""
import json

from ..adapters.database import loads


def stage_receipts(service, connection, reference, stage):
    artifact = service.row(connection, 'artifacts', reference['artifact_id'])
    owner = service.row(connection, 'tasks', artifact['task_id'])
    if (artifact['sha256'] != reference['sha256'] or owner['generation'] != reference['generation']
            or owner['artifact_id'] != artifact['id'] or owner['attempt_id'] != artifact['attempt_id']):
        raise ValueError('Candidate no longer matches the active attempt')
    file = stage + '.json'
    files = service.artifacts.read_files(artifact)
    if len(files) != 1 or files[0]['path'] != file:
        raise ValueError('Candidate must deliver the single declared stage file')
    payload = files[0]['data']
    task = owner
    definition = loads(task['definition'])
    while definition.get('root') or file in definition.get('exports', []):
        if definition.get('root'):
            children = connection.execute('SELECT * FROM tasks WHERE project_id=%s AND node_key=%s',
                (owner['project_id'], definition['result_node'])).fetchall()
        else:
            children = [row for row in connection.execute(
                'SELECT * FROM tasks WHERE parent_id=%s AND project_id=%s', (task['id'], owner['project_id']))
                if file in loads(row['definition']).get('outputs', {}) or file in loads(row['definition']).get('exports', [])]
        if len(children) != 1:
            raise ValueError('Aggregate has no unique declared producer of the stage file')
        task = children[0]
        delivered = accepted_delivery(service, connection, task)
        if [item['data'] for item in delivered if item['path'] == file] != [payload]:
            raise ValueError('Aggregate differs from the accepted producer delivery')
        definition = loads(task['definition'])
    if file not in definition.get('outputs', {}):
        raise ValueError('Stage file was not declared by the producing node')

    dependencies = connection.execute('SELECT t.* FROM dependencies d JOIN tasks t ON t.id=d.depends_on '
        'WHERE d.task_id=%s AND t.project_id=%s', (task['id'], owner['project_id'])).fetchall()
    docs, keys = {}, {}
    for upstream in dependencies:
        for item in accepted_delivery(service, connection, upstream):
            if not item['path'].endswith('.json'):
                continue
            label = item['path'][:-5]
            if label in docs:
                raise ValueError('Stage dependency file is ambiguous')
            docs[label] = json.loads(item['data'])
            keys[label] = upstream['node_key']
    keys[stage] = task['node_key']
    return owner, json.loads(payload), dependencies, docs, keys


def accepted_delivery(service, connection, task):
    if task['status'] != 'completed' or task['review'] != 'accepted':
        raise ValueError('Stage dependency is missing an accepted delivery')
    artifact = service.row(connection, 'artifacts', task['artifact_id'])
    if artifact['task_id'] != task['id'] or artifact['attempt_id'] != task['attempt_id']:
        raise ValueError('Dependency delivery belongs to a different attempt')
    return service.artifacts.read_files(artifact)
