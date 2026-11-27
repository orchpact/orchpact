"""Tool declarations, trusted bindings and immutable runtime snapshots."""
from copy import deepcopy
import unittest

from orchpact.adapters.database import loads
from orchpact.kernel.workflows import definition_digest, validate_definition
from runtime_fixtures import KernelFixture
from test_workflows import definition


def workflow():
    value = definition()
    value['resources']['tools'] = {'renderer': {
        'interface': 'document.render', 'version': '1',
        'description': 'Render a document through an authorized provider.',
        'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'},
        'effect': 'read', 'timeout_ms': 20000, 'max_output_bytes': 100000,
        'required_permissions': ['artifact:read']}}
    return value


class ToolDefinitionTests(unittest.TestCase):
    def test_legacy_digest_is_stable_and_tool_definition_is_bounded(self):
        legacy = definition()
        checked = validate_definition(legacy)
        self.assertNotIn('tools', checked['resources'])
        self.assertEqual(definition_digest(checked), definition_digest(validate_definition(legacy)))
        value = workflow()
        normalized = validate_definition(value)
        self.assertEqual(normalized['resources']['tools']['renderer']['retry_policy'], 'never')
        self.assertNotIn('tools', normalized['operations']['generate'])
        for field, secret in [('endpoint_ref', 'tool:renderer'), ('base_url', 'https://private.example'), ('credential_ref', 'private-ref'), ('api_key', 'secret')]:
            invalid = workflow();invalid['resources']['tools']['renderer'][field] = secret
            with self.subTest(field=field), self.assertRaises(ValueError):validate_definition(invalid)
        invalid = workflow();invalid['resources']['tools']['renderer']['version'] = 'latest'
        with self.assertRaises(ValueError):validate_definition(invalid)
        invalid = workflow();invalid['resources']['tools']['renderer']['input_schema'] = {'$ref': 'https://remote.example/schema'}
        with self.assertRaises(ValueError):validate_definition(invalid)
        invalid = workflow();invalid['resources']['tools']['renderer']['effect'] = 'create_artifact'
        with self.assertRaises(ValueError):validate_definition(invalid)

    def test_role_tool_grants_are_not_model_requirements(self):
        value = workflow()
        staff = value['operations']['generate']['execution_plan']['nodes'][0]['staff']
        staff['capabilities'].append('tool:renderer')
        checked = validate_definition(value)
        self.assertIn('tool:renderer',checked['operations']['generate']['execution_plan']['nodes'][0]['staff']['capabilities'])
        self.assertTrue(all('tool:renderer' not in requirement['capabilities'] for requirement in checked['resources']['models'].values()))
        invalid = deepcopy(value);invalid['operations']['generate']['execution_plan']['nodes'][0]['staff']['capabilities'][-1] = 'tool:missing'
        with self.assertRaises(ValueError):validate_definition(invalid)


class ToolWorkflowTests(KernelFixture):
    def setUp(self):
        super().setUp()
        self.value = workflow();self.register(self.value)
        self.configure_tool()

    def config(self):
        return self.client.get('/api/v1/admin/configuration').json()

    def save_config(self, body):
        body.pop('restart_required', None)
        response = self.client.put('/api/v1/admin/configuration', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def configure_tool(self, secret='tool-secret'):
        body = self.config();text = next(p for p in body['value']['providers'] if p['id'] == 'text')
        if self.config()['revision'] == 0:text['secret'] = 'text-fixture-secret'
        provider = deepcopy(text)
        provider.update(id='tool:renderer', category='tool', label='Renderer', adapter='http',
            base_url='https://tools.example.invalid/render', model='',
            search=False, thinking_disabled=False, image_options={},
            pricing={'unit': 'request', 'points': 2, 'unit_size': 1},
            tool_interface='document.render', tool_interface_version='1', implementation_version='1.0.0',
            tool_policy={'workspace_ids': [self.workspace], 'application_ids': ['fixture'], 'require_credential': True})
        if secret is not None:provider['secret'] = secret
        body['value']['providers'] = [p for p in body['value']['providers'] if p['id'] != provider['id']] + [provider]
        return self.save_config(body)

    def update_tool(self, **changes):
        body = self.config();provider = next(p for p in body['value']['providers'] if p['id'] == 'tool:renderer')
        provider.update(changes)
        return self.save_config(body)

    def bindings(self):
        return {'models': {'reasoning': 'text'}, 'tools': {'renderer': {'endpoint_ref': 'tool:renderer', 'implementation_version': '1.0.0'}}}

    def install(self):
        return self.client.post('/api/v1/apps/fixture/install', json={'bindings': self.bindings()})

    def invoke(self, key):
        return self.client.post('/api/v1/apps/fixture/runs', json={'operation': 'generate', 'input': {}, 'context': {}}, headers={'Idempotency-Key': key})

    def counts(self):
        with self.s.db.connect() as c:
            return {table:c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('units','agents','organization_packages','projects','requests','events')}

    def snapshots(self, run):
        with self.s.db.connect() as c:
            return [loads(row['snapshot']) for row in c.execute('SELECT snapshot FROM tasks WHERE project_id=%s', (run['id'],))]

    def internal_tools(self, run):
        with self.s.db.connect() as c:
            row = c.execute('SELECT binding FROM invocations WHERE id=%s', (run['id'],)).fetchone()
        return loads(row[0])['tools']

    def test_preview_install_and_export_expose_only_safe_bindings(self):
        before = self.counts()
        preview = self.client.post('/api/v1/apps/fixture/preview', json={'bindings': self.bindings()})
        self.assertEqual(preview.status_code, 200, preview.text)
        safe = preview.json()['tools']['renderer']
        self.assertEqual(safe['type'], 'http');self.assertEqual(len(safe['configuration_digest']), 64)
        self.assertEqual(self.counts(), before)
        for response in (preview, self.install()):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('tools.example.invalid', response.text)
            self.assertNotIn('_secret_ref', response.text)
            self.assertNotIn('tool-secret', response.text)
        application = self.client.get('/api/v1/apps/fixture').json()
        self.assertEqual(application['installed_bindings'], self.bindings())
        exported = self.client.get('/api/v1/apps/fixture/definition')
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json()['resources']['tools'], validate_definition(self.value)['resources']['tools'])
        self.assertNotIn('endpoint_ref', exported.text)
        with self.s.db.connect() as c:
            installed = loads(c.execute('SELECT manifest FROM organization_packages').fetchone()[0])
        self.assertNotIn('_tool_bindings', installed)

    def test_invalid_or_incompatible_bindings_roll_back(self):
        before = self.counts();valid = self.bindings()
        cases = []
        missing = deepcopy(valid);missing.pop('tools');cases.append(missing)
        extra = deepcopy(valid);extra['tools']['extra'] = extra['tools']['renderer'];cases.append(extra)
        version = deepcopy(valid);version['tools']['renderer']['implementation_version'] = '2.0.0';cases.append(version)
        unknown = deepcopy(valid);unknown['tools']['renderer']['endpoint_ref'] = 'tool:unknown';cases.append(unknown)
        secret = deepcopy(valid);secret['tools']['renderer']['secret'] = 'not-allowed';cases.append(secret)
        for bindings in cases:
            response = self.client.post('/api/v1/apps/fixture/install', json={'bindings': bindings})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(self.counts(), before)
        self.update_tool(tool_interface_version='2')
        self.assertEqual(self.install().status_code, 409)
        self.assertEqual(self.counts(), before)

    def test_workspace_and_application_policy_are_enforced(self):
        before = self.counts()
        self.update_tool(tool_policy={'workspace_ids': ['other-workspace'], 'application_ids': ['fixture'], 'require_credential': True})
        self.assertEqual(self.install().status_code, 409);self.assertEqual(self.counts(), before)
        self.update_tool(tool_policy={'workspace_ids': [self.workspace], 'application_ids': ['other-app'], 'require_credential': True})
        self.assertEqual(self.install().status_code, 409);self.assertEqual(self.counts(), before)

    def test_run_and_work_items_freeze_private_runtime_without_public_secret_leak(self):
        self.install().raise_for_status();response = self.invoke('tool-run')
        self.assertEqual(response.status_code, 201, response.text);run = response.json()
        self.assertNotIn('tools.example.invalid', response.text)
        self.assertNotIn('_secret_ref', response.text);self.assertNotIn('tool-secret', response.text)
        self.assertEqual(run['workflow']['tool_bindings']['renderer']['type'], 'http')
        internal = self.internal_tools(run)['renderer']
        self.assertEqual(internal['provider']['base_url'], 'https://tools.example.invalid/render')
        self.assertEqual(self.s.configuration.secret(internal['provider']), 'tool-secret')
        snapshots = self.snapshots(run)
        self.assertTrue(snapshots and all(snapshot['tools']['renderer']['provider'] == internal['provider'] for snapshot in snapshots))
        self.finish();self.assertEqual(self.view(run)['status'], 'completed')

    def test_semantic_configuration_change_requires_upgrade_and_preserves_retry(self):
        self.install().raise_for_status();run = self.invoke('before-tool-config').json()
        claimed = self.s.claim();old = claimed['snapshot']['tools']['renderer']
        self.s.fail(claimed['execution_context'], claimed['task']['id'], claimed['task']['attempt_id'], 'fixture retry')
        self.client.post('/api/v1/work-items/'+claimed['task']['id']+'/retry').raise_for_status()
        self.update_tool(base_url='https://changed.example.invalid/render')
        retry = self.s.claim();self.assertEqual(retry['snapshot']['tools']['renderer'], old)
        self.assertEqual(self.view(run)['workflow'], run['workflow'])
        self.assertEqual(self.invoke('after-tool-config').status_code, 409)
        self.assertEqual(self.s.configuration.secret(retry['snapshot']['tools']['renderer']['provider']), 'tool-secret')

    def test_credential_rotation_preserves_old_retry_and_new_run_uses_new_reference(self):
        self.install().raise_for_status();run = self.invoke('before-secret-rotation').json()
        claimed = self.s.claim();old = claimed['snapshot']['tools']['renderer']
        self.s.fail(claimed['execution_context'], claimed['task']['id'], claimed['task']['attempt_id'], 'fixture retry')
        self.client.post('/api/v1/work-items/'+claimed['task']['id']+'/retry').raise_for_status()
        self.update_tool(secret='rotated-tool-secret')
        retry = self.s.claim();self.assertEqual(retry['snapshot']['tools']['renderer'], old)
        self.assertEqual(self.s.configuration.secret(retry['snapshot']['tools']['renderer']['provider']), 'tool-secret')
        response = self.invoke('after-secret-rotation');self.assertEqual(response.status_code, 201, response.text)
        current = self.internal_tools(response.json())['renderer']['provider']
        self.assertEqual(self.s.configuration.secret(current), 'rotated-tool-secret')
        self.assertNotEqual(current['_secret_ref'], old['provider']['_secret_ref'])
        self.assertNotIn('rotated-tool-secret', response.text)

    def test_tool_provider_rejects_environment_credentials_and_token_pricing(self):
        body = self.config();provider = next(p for p in body['value']['providers'] if p['id'] == 'tool:renderer')
        provider['key_env'] = 'TOOL_KEY'
        response = self.client.put('/api/v1/admin/configuration', json=body)
        self.assertEqual(response.status_code, 409, response.text)
        body = self.config();provider = next(p for p in body['value']['providers'] if p['id'] == 'tool:renderer')
        provider['pricing'] = {'unit': 'tokens', 'points': 2, 'unit_size': 1000}
        response = self.client.put('/api/v1/admin/configuration', json=body)
        self.assertEqual(response.status_code, 409, response.text)
