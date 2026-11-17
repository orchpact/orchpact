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
                with self.assertRaises(SystemExit):configure(file,['--application','consumer'])
                call.assert_not_called()
    def test_independent_application_pins_asset_and_installs_bindings_separately(self):
        calls=[]
        asset={'id':'evidence-review','revision':3}
        application={'id':'consumer','revision':1,'installed_revision':None,'workflow':{'id':'evidence-review','revision':3}}
        def call(client,method,path,data=None):
            calls.append((method,path,data))
            if method=='GET':return asset if path=='workflows/evidence-review' else None
            return application
        with tempfile.TemporaryDirectory() as folder:
            binding=Path(folder)/'bindings.json';binding.write_text(json.dumps({'models':{'reasoning':'text'}}))
            with patch.dict('os.environ',ORCHPACT_API_TOKEN='fixture-only'),patch('orchpact.interfaces.client.Client.call',call):
                configure(ROOT/'examples/workflows/evidence-review.json',['--application','consumer','--install','--bindings',str(binding)])
        self.assertEqual(calls[2][1],'apps/consumer/binding')
        self.assertEqual(calls[2][2]['workflow_revision'],3)
        self.assertNotIn('bindings',calls[2][2])
        self.assertEqual(calls[3][1],'apps/consumer/install?expected_revision=0&binding_revision=1')
        self.assertEqual(calls[3][2],{'bindings':{'models':{'reasoning':'text'}}})
    def test_workflow_binding_file_is_only_sent_to_installation(self):
        calls=[]
        def call(client,method,path,data=None):
            calls.append((method,path,data))
            return None if method=='GET' else dict(revision=1,installed_revision=None,enabled=True,require_signature=True)
        with tempfile.TemporaryDirectory() as folder:
            binding=Path(folder)/'bindings.json';binding.write_text(json.dumps({'models':{'reasoning':'text'}}))
            with patch.dict('os.environ',ORCHPACT_API_TOKEN='fixture-only'),patch('orchpact.interfaces.client.Client.call',call):
                configure(ROOT/'examples/workflows/evidence-review.json',['--install','--bindings',str(binding)])
        self.assertNotIn('bindings',calls[1][2]);self.assertNotIn('require_signature',calls[1][2])
        self.assertEqual(calls[-1][1],'apps/evidence-review/install?expected_revision=0&binding_revision=1')
        self.assertEqual(calls[-1][2],{'bindings':{'models':{'reasoning':'text'}}})

    def test_public_example_has_symbolic_references_and_independent_review(self):
        manifest=validate_package(json.loads((ROOT/'examples/evidence-team.json').read_text()))
        self.assertNotIn('unit_id',json.dumps(manifest))
        nodes=manifest['operations']['calculate']['execution_plan']['nodes']
        self.assertNotEqual(nodes[0]['unit'],nodes[1]['unit']);self.assertTrue(nodes[1]['gate'])
    def test_provisioning_only_registers_data(self):
        calls=[]
        def call(client,method,path,data=None):
            calls.append((method,path,data))
            return dict(revision=7,installed_revision=3,enabled=False,require_signature=True) if method=='GET' else dict(revision=8,installed_revision=3)
        with patch.dict('os.environ',ORCHPACT_API_TOKEN='fixture-only'),patch('orchpact.interfaces.client.Client.call',call):
            configure(ROOT/'examples/evidence-team.json',['--update','--install'])
        self.assertEqual([method for method,_,_ in calls],['GET','POST','POST'])
        self.assertFalse(calls[1][2]['enabled']);self.assertTrue(calls[1][2]['require_signature'])
        self.assertEqual(calls[-1][1],'apps/evidence-team/install?expected_revision=3')
        self.assertFalse(any('/runs' in path for _,path,_ in calls))
    def test_provisioning_never_edits_an_existing_binding_without_update(self):
        calls=[]
        with patch.dict('os.environ',ORCHPACT_API_TOKEN='fixture-only'),patch('orchpact.interfaces.client.Client.call',side_effect=lambda method,path,value=None:calls.append((method,path)) or dict(revision=1)):
            configure(ROOT/'examples/evidence-team.json',[])
        self.assertEqual(calls,[('GET','apps/evidence-team')])
