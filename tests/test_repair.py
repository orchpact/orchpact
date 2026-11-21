"""Verifier-driven bounded repair reuses the existing dependency invalidation graph."""
from copy import deepcopy

from runtime_fixtures import SCHEMA
from verification_fixtures import VERIFICATION, VerificationFixture, artifact_contract, verifier_result, workflow


def declaration(path,artifact=False):
    outputs={'files':{'type':'object','required':[path],'properties':{path:deepcopy(SCHEMA)}}}
    if artifact:outputs={'artifacts':{'type':'object','required':[path],'properties':{path:{'type':'object'}}}}
    result={'interface':'document.render','version':'1','description':'Synthetic repair producer',
        'input_schema':{'type':'object','additionalProperties':False,'required':['value'],'properties':{'value':{'type':'integer'}}},
        'output_schema':{'type':'object','additionalProperties':False,'required':list(outputs),'properties':outputs},
        'effect':'create_artifact' if artifact else 'read','max_output_bytes':1000000}
    if artifact:result['artifact_contract']={'media_types':['application/pdf'],'max_files':1,'max_bytes':1000000}
    return result


def tool(key,path,dependencies=(),artifact=False,verify=False):
    node={'key':key,'kind':'tool','unit':'entry','description':'Produce '+path,'expected':path,'tool_ref':key,
        'arguments':{'value':{'$ref':'input','path':'value'}},'depends_on':list(dependencies),
        'outputs':{} if artifact else {path:deepcopy(SCHEMA)},'artifact_outputs':{path:artifact_contract()} if artifact else {}}
    if not artifact:node.pop('artifact_outputs')
    if verify:node['verification']=deepcopy(VERIFICATION)
    return node


def repair_workflow(shared=False,max_repairs=2):
    value=workflow();verifier=value['resources']['tools']['verifier']
    nodes=[tool('facts','facts.json'),tool('sibling','sibling.json')]
    if shared:
        nodes += [tool('doc_a','doc_a.pdf',['facts'],True,True),tool('doc_b','doc_b.pdf',['facts'],True,True)]
    else:
        nodes += [tool('render','deployment.pdf',['facts','sibling'],True,True)]
    value['resources']['tools']={node['key']:declaration(node['expected'],node['expected'].endswith('.pdf')) for node in nodes}
    value['resources']['tools']['verifier']=verifier
    result=nodes[-1] if not shared else nodes[-2]
    operation=value['operations']['generate']
    operation.update(output_file=result['expected'],artifact_output=artifact_contract(),tool_limit=12,
        execution_plan={'nodes':nodes,'result_node':result['key'],'max_repairs':max_repairs})
    return value


class RepairTests(VerificationFixture):
    def install_case(self,value):
        self.verifier.result=self.fail_at('facts')
        self.register(value)
        bindings={'models':{},'tools':{}}
        for slot in value['resources']['tools']:
            bindings['tools'][slot]={'endpoint_ref':'tool:verifier' if slot=='verifier' else 'tool:renderer','implementation_version':'1.0.0'}
        response=self.client.post('/api/v1/apps/fixture/install',json={'bindings':bindings})
        self.assertEqual(response.status_code,200,response.text)

    def fail_at(self,node_key,status='FAIL'):
        def result(context):
            return verifier_result(status,checks=[dict(check_id='structure',status=status,code='invalid_source',node_key=node_key,
                artifact_path='facts.json',field_path='value',message='synthetic source failure',repair='Correct the source')])
        return result

    @staticmethod
    def tasks(view):
        return {task['node_key']:task for task in view['tasks'] if task['node_key']}

    def test_single_document_repair_preserves_independent_sibling_and_history(self):
        value=repair_workflow();self.install_case(value);run=self.invoke()
        for _ in range(3):self.assertTrue(self.app.state.runner.step())
        tasks=self.tasks(self.view(run));sibling_artifact=tasks['sibling']['artifact_id']
        self.assertEqual(tasks['facts']['generation'],2);self.assertEqual(tasks['render']['generation'],2)
        self.assertEqual(tasks['sibling']['generation'],1);self.assertEqual(tasks['sibling']['turns'],1)
        self.assertEqual(self.view(run)['execution_plan']['repairs'],1)
        self.assertEqual(self.view(run)['verification_evidence'][0]['status'],'FAIL')
        self.verifier.result=verifier_result()
        for _ in range(2):self.assertTrue(self.app.state.runner.step())
        view=self.view(run);tasks=self.tasks(view)
        self.assertEqual(view['status'],'completed');self.assertEqual(self.renderer.calls,3);self.assertEqual(self.verifier.calls,2)
        self.assertEqual(tasks['sibling']['turns'],1);self.assertEqual(tasks['sibling']['artifact_id'],sibling_artifact)
        self.assertEqual({row['status'] for row in view['verification_evidence']},{'FAIL','PASS'})
        with self.s.db.connect() as c:
            self.assertTrue(c.execute('SELECT 1 FROM artifacts WHERE id=%s',(sibling_artifact,)).fetchone())
            self.assertEqual(c.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],6)

    def test_shared_fact_invalidates_all_dependents_but_not_independent_branch(self):
        value=repair_workflow(shared=True);self.install_case(value);run=self.invoke()
        for _ in range(3):self.assertTrue(self.app.state.runner.step())
        tasks=self.tasks(self.view(run))
        self.assertEqual(tasks['facts']['generation'],2);self.assertEqual(tasks['doc_a']['generation'],2);self.assertEqual(tasks['doc_b']['generation'],2)
        self.assertEqual(tasks['sibling']['generation'],1);self.assertEqual(tasks['sibling']['turns'],1)
        self.verifier.result=verifier_result()
        for _ in range(3):self.assertTrue(self.app.state.runner.step())
        view=self.view(run);tasks=self.tasks(view)
        self.assertEqual(view['status'],'completed');self.assertEqual(tasks['doc_b']['generation'],2)
        self.assertEqual(tasks['sibling']['generation'],1);self.assertEqual(self.verifier.calls,3)

    def test_unknown_location_falls_back_to_current_verified_node(self):
        value=repair_workflow();self.install_case(value);run=self.invoke();self.verifier.result=self.fail_at('not-a-real-node')
        for _ in range(3):self.assertTrue(self.app.state.runner.step())
        tasks=self.tasks(self.view(run))
        self.assertEqual(tasks['render']['generation'],2);self.assertEqual(tasks['facts']['generation'],1)
        self.assertEqual(tasks['sibling']['generation'],1);self.assertEqual(self.view(run)['execution_plan']['repairs'],1)

    def test_repair_budget_exhaustion_stops_without_queueing(self):
        value=repair_workflow(max_repairs=0);self.install_case(value);run=self.invoke()
        for _ in range(3):self.assertTrue(self.app.state.runner.step())
        view=self.view(run);tasks=self.tasks(view)
        self.assertEqual(view['execution_plan']['repairs'],0);self.assertEqual(tasks['render']['generation'],1)
        self.assertEqual(tasks['render']['status'],'failed');self.assertIn('返工额度已用尽',tasks['render']['error'])
        self.assertFalse(self.app.state.runner.step())

    def test_inconclusive_never_enters_automatic_repair(self):
        value=repair_workflow();self.install_case(value);run=self.invoke();self.verifier.result=self.fail_at('facts','INCONCLUSIVE')
        for _ in range(3):self.assertTrue(self.app.state.runner.step())
        view=self.view(run);tasks=self.tasks(view)
        self.assertEqual(view['execution_plan']['repairs'],0);self.assertEqual(tasks['render']['status'],'failed')
        self.assertEqual(tasks['render']['generation'],1);self.assertEqual(tasks['facts']['generation'],1)
        self.assertFalse(self.app.state.runner.step())


if __name__=='__main__':unittest.main()
