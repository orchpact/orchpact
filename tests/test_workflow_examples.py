"""Packaged walkthroughs use the same admission and policy snapshots as other workflows."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from account_fixtures import create_user
from orchpact.kernel.contracts import validate
from runtime_fixtures import KernelFixture


PATH = '/api/v1/workbench/examples'


class ExampleTests(KernelFixture):
    def test_preview_and_explicit_install_preserve_workflow_and_review_scope(self):
        initial = self.client.get(PATH).json()
        self.assertEqual([row['id'] for row in initial], ['fulfillment', 'mystery-story'])
        self.assertEqual(self.client.get(PATH+'/unknown').status_code, 404)
        for entry in initial:
            with self.subTest(example=entry['id']):
                path = PATH+'/'+entry['id']
                detail = self.client.get(path).json()
                validate(detail['input'], detail['definition']['operations'][detail['operation']]['input_schema'])
                self.assertIsNone(entry['revision'])
                self.assertTrue(all('secret' not in slot for slot in detail['definition']['resources'].values()))
                with patch.dict('os.environ', {'RUNTIME_FIXTURE_KEY':'fixture-no-network'}):
                    prepared = self.client.post(path+'/prepare')
                    prepared.raise_for_status()
                    self.assertEqual(self.client.post(path+'/prepare').json(), prepared.json())
                    data = prepared.json()
                    body = dict(workflow_revision=data['revision'], expected_revision=0,
                                bindings=dict(models={'reasoning':'text'}, tools={
                                    'verifier':dict(endpoint_ref=data['provider_id'], implementation_version='1.0.0'),
                                }))
                    response = self.client.post(path+'/install', json=body)
                    response.raise_for_status()
                    self.assertEqual(self.client.post(path+'/install', json=body).status_code, 409)
                installed = self.s.delivery.installation(self.ctx, entry['workflow_id'])
                self.assertEqual(installed['workflow_revision'], 1)
                self.assertEqual(self.client.get('/api/v1/workflows/'+entry['workflow_id']).json()['sha256'], detail['sha256'])
        with self.s.db.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM workflows').fetchone()[0], 2)
            rows = connection.execute('SELECT scope FROM policies').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], 'unit:' + connection.execute(
                "SELECT id FROM units WHERE name='Human publication desk' AND workspace_id=%s",
                (self.workspace,)).fetchone()[0])
            self.assertTrue(self.s.policies.read(connection, rows[0][0])['policy']['independent_review'])
            self.assertEqual(connection.execute('SELECT count(*) FROM projects').fetchone()[0], 0)
        self.assertTrue(all(row['installed_revision'] == 1 for row in self.client.get(PATH).json()))

    def test_conflicts_and_non_admin_cannot_prepare_examples(self):
        path = PATH+'/fulfillment'
        definition = self.client.get(path).json()['definition']
        definition['name'] = 'User-owned definition'
        self.client.post('/api/v1/workflows', json=definition).raise_for_status()
        response = self.client.post(path+'/prepare')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'example_workflow_conflict')
        self.assertIsNone(self.s.configuration.record('tool:example-fulfillment'))
        self.assertTrue(self.client.get(PATH).json()[0]['conflict'])
        client = TestClient(self.app)
        try:
            create_user(self.s, client, 'example-owner', 'fixture-password-1234')
            self.assertEqual(client.get(PATH).status_code, 403)
            self.assertEqual(client.get(path).status_code, 403)
            self.assertEqual(client.post(path+'/prepare').status_code, 403)
        finally:
            client.close()
