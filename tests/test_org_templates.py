"""Portable public examples and shared provisioning, with no sibling repositories."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from orchpact.kernel.packages import validate_package
from orchpact.interfaces.client import configure

ROOT=Path(__file__).resolve().parents[1]

class OrganizationTemplateTests(unittest.TestCase):
    def test_definition_cannot_change_privileged_client_request_paths(self):
        import io
        value=json.loads((ROOT/'examples/workflows/evidence-review.json').read_text());value['id']='app/../../admin/users'
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'untrusted.json';file.write_text(json.dumps(value))
            with patch.dict('os.environ',ORCHPACT_API_TOKEN='fixture-only'),patch('orchpact.interfaces.client.Client.call') as call,patch('sys.stderr',new_callable=io.StringIO):
                with self.assertRaises(SystemExit):configure(file,[])
                call.assert_not_called()
    def test_direct_workflow_installation_keeps_bindings_out_of_published_definition(self):
        calls=[]
        def call(client,method,path,data=None):
            calls.append((method,path,data))
            if method=='GET':return None
            if path.startswith('workflows?'):return dict(id='evidence-review',revision=1)
            return dict(installed_revision=0,revision=1)
        with tempfile.TemporaryDirectory() as folder:
            binding=Path(folder)/'bindings.json';binding.write_text(json.dumps({'models':{'reasoning':'text'}}))
            with patch.dict('os.environ',ORCHPACT_API_TOKEN='fixture-only'),patch('orchpact.interfaces.client.Client.call',call):
                configure(ROOT/'examples/workflows/evidence-review.json',['--install','--bindings',str(binding)])
        self.assertNotIn('bindings',calls[1][2])
        self.assertEqual(calls[-1][1],'workflows/evidence-review/install')
        self.assertEqual(calls[-1][2],dict(workflow_revision=1,expected_revision=0,bindings={'models':{'reasoning':'text'}}))
        self.assertFalse(any('apps/' in path or '/runs' in path for _,path,_ in calls))

    def test_public_example_has_symbolic_references_and_independent_review(self):
        manifest=validate_package(json.loads((ROOT/'examples/evidence-team.json').read_text()))
        self.assertNotIn('unit_id',json.dumps(manifest))
        nodes=manifest['operations']['calculate']['execution_plan']['nodes']
        self.assertNotEqual(nodes[0]['unit'],nodes[1]['unit']);self.assertTrue(nodes[1]['gate'])
    def test_publish_does_not_upgrade_or_execute_without_explicit_flags(self):
        calls=[]
        with patch.dict('os.environ',ORCHPACT_API_TOKEN='fixture-only'),patch('orchpact.interfaces.client.Client.call',side_effect=lambda method,path,value=None:calls.append((method,path)) or dict(revision=1)):
            configure(ROOT/'examples/workflows/evidence-review.json',[])
        self.assertEqual(calls,[('GET','workflows/evidence-review')])
