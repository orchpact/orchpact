"""Fixed workflow templates, authoritative previews, and independent publication."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from orchpact.kernel.templates import JSON_DELIVERY, compile_template, list_templates, metadata, preview_template
from orchpact.kernel.workflows import definition_digest, validate_definition
from runtime_fixtures import KernelFixture


def parameters():
    return {'workflow_name': 'Template example', 'instruction': 'Deliver the requested JSON result'}


class TemplateTests(unittest.TestCase):
    def test_catalog_defaults_and_explicit_parameters_are_equivalent(self):
        catalog = list_templates()
        self.assertEqual([item['template_id'] for item in catalog], ['json-delivery'])
        item = catalog[0]
        implicit = preview_template(item['template_id'], item['sha256'], parameters())
        explicit_values = dict(parameters(), workflow_version='1.0.0', unit_name='处理组', request_limit=32)
        explicit = preview_template(item['template_id'], item['sha256'], explicit_values)
        self.assertEqual(implicit['definition'], explicit['definition'])
        self.assertEqual(implicit['sha256'], definition_digest(validate_definition(implicit['definition'])))
        self.assertEqual(implicit['definition']['operations']['generate']['execution_plan']['result_node'], 'deliver')
        self.assertNotIn('sha256', implicit['definition'])

    def test_supported_scalar_array_nested_and_enum_parameters(self):
        template = deepcopy(JSON_DELIVERY)
        properties = template['parameters_schema']['properties']
        properties.update(
            enabled={'type': 'boolean', 'default': True},
            ratio={'type': 'number', 'default': 0.5, 'minimum': 0, 'maximum': 1},
            tags={'type': 'array', 'items': {'type': 'string'}, 'default': ['one', 'two']},
            mode={'type': 'string', 'enum': ['safe', 'strict'], 'default': 'strict'},
            nested={'type': 'object', 'additionalProperties': False, 'default': {}, 'properties': {
                'label': {'type': 'string', 'default': 'inside'},
            }},
        )
        result = compile_template(template, parameters())
        self.assertEqual(result['parameters']['enabled'], True)
        self.assertEqual(result['parameters']['ratio'], 0.5)
        self.assertEqual(result['parameters']['tags'], ['one', 'two'])
        self.assertEqual(result['parameters']['mode'], 'strict')
        self.assertEqual(result['parameters']['nested'], {'label': 'inside'})

    def test_parameter_default_schema_and_runtime_injection_rejections(self):
        item = list_templates()[0]
        cases = [
            {},
            dict(parameters(), extra=True),
            dict(parameters(), instruction=1),
            dict(parameters(), request_limit=0),
            dict(parameters(), request_limit=True),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                preview_template(item['template_id'], item['sha256'], value)
        invalid = deepcopy(JSON_DELIVERY); invalid['parameters_schema']['properties']['request_limit']['default'] = 0
        with self.assertRaisesRegex(ValueError, 'request_limit'): metadata(invalid)
        invalid = deepcopy(JSON_DELIVERY); invalid['parameters_schema']['properties']['bad'] = {'type': 'array', 'items': {'type': 'object'}}
        with self.assertRaisesRegex(ValueError, '标量'): metadata(invalid)
        invalid = deepcopy(JSON_DELIVERY); invalid['definition']['id'] = {'$param': 'unknown'}
        with self.assertRaisesRegex(ValueError, '参数引用不存在'): compile_template(invalid, parameters())
        for mutate in (
            lambda value: value['definition']['units'][0].update(model_id={'$param': 'workflow_name'}),
            lambda value: value['definition'].update(endpoint={'$param': 'workflow_name'}),
        ):
            invalid = deepcopy(JSON_DELIVERY); mutate(invalid)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): compile_template(invalid, parameters())

    def test_component_templates_require_an_explicit_fixed_directory(self):
        from test_components import author, component
        source = component(); template = deepcopy(JSON_DELIVERY); template['definition'] = author(source)
        with self.assertRaisesRegex(ValueError, '显式本地组件目录'): compile_template(template, parameters())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'document.generate@1.0.0.json').write_text(json.dumps(source))
            result = compile_template(template, parameters(), root)
        self.assertEqual(result['component_report']['expander_version'], '1')
        self.assertEqual(len(result['component_report']['components']), 2)
        self.assertNotIn('components', result['definition'])


class TemplateHttpTests(KernelFixture):
    def counts(self):
        with self.s.db.connect() as connection:
            return {table: connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                for table in ('workflows', 'workflow_versions', 'applications', 'application_bindings', 'projects', 'events')}

    def test_preview_is_admin_only_read_only_and_publication_is_explicit(self):
        before = self.counts()
        response = self.client.get('/api/v1/workflow-templates')
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()[0]
        path = '/api/v1/workflow-templates/json-delivery/preview'
        response = self.client.post(path, json={'sha256': item['sha256'], 'parameters': parameters()})
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertEqual(preview['sha256'], definition_digest(preview['definition']))
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.client.post(path, json={'sha256': '0' * 64, 'parameters': parameters()}).status_code, 409)
        self.assertEqual(self.client.post('/api/v1/workflow-templates/missing/preview', json={'sha256': '0' * 64, 'parameters': {}}).status_code, 404)
        self.assertEqual(self.counts(), before)
        published = self.client.post('/api/v1/workflows', json=preview['definition'])
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()['revision'], 1)
        self.assertEqual(published.json()['sha256'], preview['sha256'])

    def test_non_admin_cannot_read_or_preview_templates(self):
        from account_fixtures import create_user
        client = TestClient(self.app)
        try:
            create_user(self.s, client, 'template-reader', 'fixture-password-1234')
            self.assertEqual(client.get('/api/v1/workflow-templates').status_code, 403)
            self.assertEqual(client.post('/api/v1/workflow-templates/json-delivery/preview', json={'sha256': '0' * 64, 'parameters': {}}).status_code, 403)
        finally:
            client.close()


if __name__ == '__main__': unittest.main()
