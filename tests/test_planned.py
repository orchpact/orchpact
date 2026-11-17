"""Multi-level native contracts use the workflow scheduler and immutable graph."""
import copy
from runtime_fixtures import KernelFixture,package,SCHEMA
from orchpact.kernel.plan import validate_plan

class PlannedTests(KernelFixture):
    def complex(self):
        value=package();op=value['operations']['generate'];writer=op['execution_plan']['nodes'][0]
        source=copy.deepcopy(writer);source.update(key='source',parent='nested',outputs={'source.json':SCHEMA})
        writer['depends_on']=['team']
        op['execution_plan']['nodes']=[writer,dict(key='team',description='team',expected='files',exports=['source.json']),dict(key='nested',parent='team',description='nested',expected='files',exports=['source.json']),source]
        return value
    def test_forward_references_nested_groups_and_no_model_aggregation(self):
        run=self.invoke(self.complex());ctx=self.s.claim();self.assertEqual(ctx['planned_node']['key'],'source')
        self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'source',[dict(path='source.json',content='{"value":3}')])
        ctx=self.s.claim();self.assertEqual(ctx['planned_node']['key'],'writer');self.assertTrue(ctx['inputs'])
        self.finish(ctx,4);self.assertEqual(self.view(run)['result'],{'value':4})
        with self.s.db.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM attempts WHERE model_id!='deterministic'").fetchone()[0],2)
    def test_cycles_ancestor_dependencies_and_duplicate_aliases(self):
        for mode in ('cycle','ancestor','duplicate'):
            value=self.complex();nodes=value['operations']['generate']['execution_plan']['nodes']
            if mode=='cycle':nodes[-1]['depends_on']=['writer']
            if mode=='ancestor':nodes[-1]['depends_on']=['team']
            if mode=='duplicate':nodes[-1]['key']='writer'
            with self.subTest(mode=mode):self.assertEqual(self.client.post('/api/v1/organization-packages',json=value).status_code,409)
    def test_plan_schema_preserves_local_references(self):
        value=package();schema={'type':'object','$defs':{'integer':{'type':'integer'}},'required':['value'],'properties':{'value':{'$ref':'#/$defs/integer'}}}
        op=value['operations']['generate'];op['output_schema']=schema;op['execution_plan']['nodes'][0]['outputs']['result.json']=schema
        run=self.invoke(value);self.finish(value=8);self.assertEqual(self.view(run)['result'],{'value':8})
    def test_failed_stage_requires_explicit_retry_and_reuses_graph(self):
        run=self.invoke(self.complex());ctx=self.s.claim();self.s.fail(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'provider unknown')
        self.assertIsNone(self.s.claim());before={t['id'] for t in self.view(run)['tasks']}
        self.client.post('/api/v1/work-items/'+ctx['task']['id']+'/retry').raise_for_status()
        self.assertEqual(self.s.claim()['task']['id'],ctx['task']['id']);self.assertEqual({t['id'] for t in self.view(run)['tasks']},before)
    def test_unexpected_or_invalid_json_files_are_not_published(self):
        run=self.invoke();ctx=self.s.claim()
        for files in ([dict(path='wrong.json',content='{"value":1}')],[dict(path='result.json',content='{"value":"bad"}')],[dict(path='result.json',content='{"value":1,"value":2}')]):
            with self.assertRaises(ValueError):self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'invalid',files)
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],0)
    def test_rule_validation_precedes_installation(self):
        value=package();value['operations']['generate']['execution_plan']['nodes'][0]['rules']=[dict(kind='unknown',file='result.json')]
        self.assertEqual(self.client.post('/api/v1/organization-packages',json=value).status_code,409)
