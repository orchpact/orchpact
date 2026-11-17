"""Governance policies are scoped rules, with no privileged department."""
from runtime_fixtures import KernelFixture,package,SCHEMA

class GovernancePolicyTests(KernelFixture):
    def policy(self,scope,revision=0,**policy):return self.client.put('/api/v1/policies/'+scope,json=dict(revision=revision,policy=policy))
    def test_invalid_schema_is_rejected_at_policy_save(self):
        self.assertEqual(self.policy('platform',input_schema={'$ref':'https://invalid/schema'}).status_code,409)
        self.assertEqual(self.client.get('/api/v1/policies/platform').json()['revision'],0)
    def test_input_rule_blocks_task_creation_atomically(self):
        self.policy('platform',input_schema={'type':'object','required':['impossible']}).raise_for_status()
        self.register();response=self.client.post('/api/v1/apps/fixture/runs',json=dict(operation='generate',input={}),headers={'Idempotency-Key':'bad'})
        self.assertEqual(response.status_code,409)
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM projects').fetchone()[0],0)
    def test_delivery_policy_snapshot_survives_rule_change(self):
        self.policy('platform',max_output_bytes=12).raise_for_status();run=self.invoke()
        self.policy('platform',revision=1,max_output_bytes=10000).raise_for_status()
        ctx=self.s.claim()
        with self.assertRaises(ValueError):self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'too large',[dict(path='result.json',content='{"value":100000}')])
    def test_workspace_policy_isolated_from_other_spaces(self):
        self.policy('workspace:other',input_schema={'required':['absent']}).raise_for_status()
        self.invoke();self.finish()
    def test_contract_rule_does_not_mutate_package(self):
        self.policy('platform',leader_outputs={'audit.json':{'type':'object'}}).raise_for_status()
        run=self.invoke();self.finish()
        self.assertNotEqual(self.view(run)['status'],'completed')
