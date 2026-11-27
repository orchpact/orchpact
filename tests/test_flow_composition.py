from copy import deepcopy

from runtime_fixtures import KernelFixture, package
from orchpact.kernel.packages import validate_package


class FlowCompositionTests(KernelFixture):
    def test_disjoint_nodes_with_same_nested_filename_validate_their_own_contracts(self):
        value = package()
        operation = value['operations']['generate']
        result = 'reports/result.json'
        operation['output_file'] = result
        plan = operation['execution_plan']
        writer = plan['nodes'][0]
        writer['outputs'] = {result: operation['output_schema']}
        branch = deepcopy(writer)
        branch.update(key='branch', outputs={result: {'type':'object','required':['letter'],
                                                      'properties':{'letter':{'type':'string'}}}})
        plan['nodes'].append(branch)
        run = self.invoke(value)
        for index in range(2):
            task = self.s.claim()
            files = [dict(path=result, content='{"value":7}' if task['planned_node']['key']=='writer' else '{"letter":"b"}')]
            self.s.complete_work(task['execution_context'],task['task']['id'],task['task']['attempt_id'],'deliver',files)
            if not index:
                self.assertNotEqual(self.view(run)['status'],'completed')
        view = self.view(run)
        self.assertEqual(view['status'],'completed')
        self.assertEqual(view['result'],{'value':7})
        self.assertEqual(self.client.get('/api/v1/artifacts/'+view['artifact_id']+'/download').json(),{'value':7})

    def test_root_output_rejects_unsafe_paths_and_non_json_results(self):
        for path in ('../outside.json','/abs.json','nested/../file.json','dir\\file.json','a//b.json','dir/image.png',None):
            with self.subTest(path=path), self.assertRaises(ValueError):
                value=package()
                value['operations']['generate']['output_file']=path
                validate_package(value)

    def check_example_receipt(self, stage, verifier_class, original, candidate):
        import json
        from types import SimpleNamespace
        value=package()
        op=value['operations']['generate']
        file=stage+'.json'
        op.update(output_file=file, output_schema={'type':'object'})
        plan=op['execution_plan']
        author=plan['nodes'][0]
        author.update(key=stage+'-2', parent='checkpoint', outputs={file:op['output_schema']})
        # An identically named delivery elsewhere in the same project must never be substituted.
        other=deepcopy(author)
        other.update(key=stage)
        other.pop('parent')
        plan.update(result_node=author['key'], nodes=[author,other,
            dict(key='checkpoint',kind='group',description='Verify the workflow',expected=file,exports=[file])])
        self.register(value)
        response=self.client.post('/api/v1/apps/fixture/runs',json=dict(operation='generate',input=original,context={}),
                                  headers={'Idempotency-Key':'verify-'+stage})
        response.raise_for_status()
        run=response.json()
        for _ in range(2):
            task=self.s.claim()
            correct=task['planned_node']['key']==author['key']
            self.s.complete_work(task['execution_context'],task['task']['id'],task['task']['attempt_id'],'deliver',
                                 [dict(path=file,content=json.dumps(candidate if correct else {}))])
        view=self.view(run)
        self.assertEqual(view['status'],'completed')
        judge=verifier_class(self.s)
        for owner in (None,'checkpoint'):
            task=next(t for t in view['tasks'] if t['node_key']==owner)
            with self.s.db.connect() as c:
                artifact=self.s.row(c,'artifacts',task['artifact_id'])
            reference=dict(artifact_id=artifact['id'],sha256=artifact['sha256'],generation=task['generation'])
            context=SimpleNamespace(arguments=dict(artifact=reference,parameters=dict(stage=stage)))
            report=judge.invoke(context)
            self.assertEqual(report['status'],'PASS')
            self.assertEqual(report['checks'][0]['node_key'],author['key'])
            reference['generation']+=1
            with self.assertRaises(ValueError):
                judge.invoke(context)

    def test_fulfillment_verifier_binds_the_scoped_delivery_and_generation(self):
        from orchpact.scenarios.fulfillment import FulfillmentVerifier
        original={'orders':[{'id':'order'}]}
        self.check_example_receipt('demand',FulfillmentVerifier,original,original)

    def test_story_verifier_binds_the_scoped_delivery_and_generation(self):
        from orchpact.scenarios.mystery import StoryVerifier
        self.check_example_receipt('style',StoryVerifier,{'editorial_rubric':['dialogue']},{'rubric':['dialogue']})
