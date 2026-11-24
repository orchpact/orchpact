"""Report strategy is declared in a workflow; the runtime only enforces contracts."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx

from orchpact.adapters.database import dumps, loads
from runtime_fixtures import KernelFixture

HttpClient=httpx.Client
SOURCES=[dict(id='S1',text='First supplied record'),dict(id='S2',text='Second supplied record'),dict(id='S3',text='Third supplied record')]
SELECTION=dict(selected_ids=['S1','S3'],reason='Cover the requested topics')
REPORT=dict(body='S1 and S3 support this synthetic summary; other claims remain unverified.',source_ids=['S1','S3'])


class ExplicitReferenceTests(KernelFixture):
    def setUp(self):
        super().setUp()
        self.value=json.loads((Path(__file__).resolve().parents[1]/'examples/workflows/explicit-reference-report.json').read_text())
        self.value['id']='fixture'
        response=self.client.post('/api/v1/workflows',json=self.value)
        self.assertEqual(response.status_code,200,response.text)
        response=self.client.post('/api/v1/workflows/fixture/install',json={'workflow_revision':1,'expected_revision':0,'bindings':{'models':{'reasoning':'text'}}})
        self.assertEqual(response.status_code,200,response.text)

    def view(self,run):
        return self.client.get('/api/v1/runs/'+run['id']).json()

    def start(self):
        return self.s.delivery.invoke(self.ctx,'fixture',dict(operation='generate',input=dict(sources=SOURCES),context={}), 'explicit-report')

    def deliver(self,ctx,path,value):
        return self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'Synthetic delivery',[
            dict(path=path,content=dumps(value))])

    def transport(self,repair=False):
        self.calls=[]
        def send(request):
            body=json.loads(request.content);inputs=json.loads(body['messages'][1]['content'])
            key=inputs['stage_contract']['key'];self.calls.append((key,deepcopy(inputs)))
            self.assertNotIn('reference_contracts',inputs)
            payload={'evidence':{'evidence.json':dict(sources=SOURCES)},'select':{'selection.json':SELECTION},'report':{'result.json':REPORT}}[key]
            if repair and key=='report' and not inputs.get('output_feedback'):
                payload={'result.json':dict(body='Unselected citation S2',source_ids=['S1','S3'])}
            return httpx.Response(200,json=dict(id='synthetic-reference-response',choices=[dict(finish_reason='stop',message=dict(content=dumps(dict(files=payload))))],usage={'total_tokens':10}))
        stack=ExitStack()
        stack.enter_context(patch.dict(os.environ,{'RUNTIME_FIXTURE_KEY':'synthetic-only'}))
        stack.enter_context(patch('orchpact.adapters.providers.httpx.Client',side_effect=lambda **kw:HttpClient(transport=httpx.MockTransport(send),**kw)))
        return stack

    def test_declared_nodes_are_the_only_model_calls(self):
        run=self.start()
        with self.transport():
            for _ in range(3):self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(view['status'],'completed');self.assertEqual(view['result'],REPORT)
        self.assertEqual([key for key,_ in self.calls],['evidence','select','report'])
        self.assertEqual(len(view['requests']),3);self.assertEqual(len(view['crew_runs']),3)
        self.assertEqual(view['workflow']['id'],'fixture')
        with self.s.db.connect() as c:
            definition=loads(c.execute("SELECT definition FROM tasks WHERE project_id=%s AND node_key='report'",(run['id'],)).fetchone()[0])
        self.assertEqual(definition['outputs'],self.value['operations']['generate']['execution_plan']['nodes'][2]['outputs'])

    def test_selection_rejects_unknown_duplicate_or_excess_sources(self):
        run=self.start();self.deliver(self.s.claim(),'evidence.json',dict(sources=SOURCES));ctx=self.s.claim()
        self.assertEqual(ctx['task']['node_key'],'select')
        for ids in (['S1','S9'],['S1','S2','S3'],['S1','S1'],[]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                self.deliver(ctx,'selection.json',dict(selected_ids=ids,reason='Synthetic selection'))
        with self.s.db.connect() as c:
            self.assertIsNone(self.s.row(c,'tasks',ctx['task']['id'])['artifact_id'])
        self.assertNotEqual(self.view(run)['status'],'completed')

    def test_report_must_preserve_selection_and_close_text_references(self):
        run=self.start();self.deliver(self.s.claim(),'evidence.json',dict(sources=SOURCES))
        self.deliver(self.s.claim(),'selection.json',SELECTION);ctx=self.s.claim()
        for report in (dict(body='S1',source_ids=['S1']),dict(body='S1 S2',source_ids=['S1','S2']),dict(body='S12',source_ids=['S1','S3']),dict(body='S1\nS3\nS2',source_ids=['S1','S3'])):
            with self.subTest(report=report),self.assertRaises(ValueError):self.deliver(ctx,'result.json',report)
        self.deliver(ctx,'result.json',REPORT)
        self.assertEqual(self.view(run)['result'],REPORT)

    def test_generic_bounded_correction_keeps_the_same_contract(self):
        run=self.start()
        with self.transport(repair=True):
            for _ in range(3):self.assertTrue(self.app.state.runner.step())
        self.assertEqual(self.view(run)['result'],REPORT)
        self.assertEqual([key for key,_ in self.calls],['evidence','select','report','report'])
        first,last=[inputs for key,inputs in self.calls if key=='report']
        self.assertEqual(first['stage_contract'],last['stage_contract'])
        self.assertIn('output_feedback',last)
        self.assertEqual(len(self.view(run)['requests']),4)
