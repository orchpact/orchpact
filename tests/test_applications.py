"""Dynamic organization decisions through the same workflow graph as native plans."""
from runtime_fixtures import KernelFixture,package
from orchpact.adapters.database import dumps
from orchpact.kernel.errors import DomainError,Forbidden

class ApplicationTests(KernelFixture):
    def start(self):
        run=self.invoke(package(planned=False));return run,self.s.claim()
    def decide(self,ctx,action,**kw):
        return self.s.decide(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],dict(action=action,summary='fixture decision',**kw))
    def delegate(self,ctx):
        self.decide(ctx,'plan',recruits=[dict(name='writer',role='writer',prompt='write',reason='specialist',capabilities=['text','json'])],assignments=[dict(agent='writer',description='write',expected='result.json')])
        return self.s.claim()
    def test_dynamic_recruit_execute_review_and_forward(self):
        run,leader=self.start();worker=self.delegate(leader);self.finish(worker,9)
        leader=self.s.claim();child=next(t for t in self.view(run)['tasks'] if t['id']==worker['task']['id'])
        self.decide(leader,'deliver',result='verified',source_artifact_id=child['artifact_id'],reviews=[dict(task_id=child['id'],verdict='accept',feedback='valid')])
        self.assertEqual(self.view(run)['result'],{'value':9})
    def test_recruitment_capability_failure_is_atomic(self):
        run,ctx=self.start()
        with self.assertRaises(ValueError):self.decide(ctx,'plan',recruits=[dict(name='image',role='image',reason='image',capabilities=['image'])])
        self.assertEqual(len(self.view(run)['tasks']),1)
        with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM agents').fetchone()[0],0)
    def test_delivery_requires_final_contract_and_explicit_result(self):
        run,ctx=self.start()
        for kw in [dict(result='report'),dict(result='report',files=[dict(path='result.json',content='{"value":"wrong"}')]),dict(files=[dict(path='result.json',content='{"value":1}')])]:
            with self.assertRaises(ValueError):self.decide(ctx,'deliver',**kw)
        self.assertEqual(self.view(run)['status'],'running')
    def test_questions_wait_for_answer_and_reply_is_idempotent(self):
        run,ctx=self.start();self.decide(ctx,'wait',messages=[dict(to_task='user',kind='question',content='which value?')])
        self.assertIsNone(self.s.claim());q=self.view(run)['questions'][0]['id']
        path='/api/v1/questions/'+q+'/answers'
        self.assertEqual(self.client.post(path,json={'content':'7'}).status_code,200)
        self.assertEqual(self.client.post(path,json={'content':'7'}).status_code,200)
        self.assertEqual(self.client.post(path,json={'content':'8'}).status_code,409)
        ctx=self.s.claim();self.decide(ctx,'deliver',result='answered',files=[dict(path='result.json',content='{"value":7}')])
        self.assertEqual(self.view(run)['result'],{'value':7})
    def test_empty_wait_does_not_create_busy_loop(self):
        run,ctx=self.start()
        with self.assertRaises(ValueError):self.decide(ctx,'wait')
    def test_unreviewed_child_cannot_be_forwarded(self):
        run,ctx=self.start();worker=self.delegate(ctx);self.finish(worker);ctx=self.s.claim()
        with self.assertRaises(ValueError):self.decide(ctx,'deliver',result='unreviewed',source_artifact_id=self.view(run)['tasks'][-1]['artifact_id'])
    def test_cross_organization_intake_accepts_before_recruitment(self):
        run,ctx=self.start();u=self.s.unit(self.ctx,dict(name='partner',purpose='independent',prompt='',model_id='text'))
        self.decide(ctx,'plan',assignments=[dict(unit_id=u['id'],description='help',expected='result.json')])
        intake=self.s.claim();self.assertEqual(intake['task']['phase'],'intake')
        with self.assertRaises(ValueError):self.decide(intake,'plan',recruits=[dict(name='writer',role='writer',reason='write')])
        self.decide(intake,'accept');self.assertEqual(self.s.claim()['task']['phase'],'plan')
    def test_rejected_child_can_be_waived_but_result_stays_partial(self):
        run,ctx=self.start();u=self.s.unit(self.ctx,dict(name='partner',purpose='independent',prompt='',model_id='text'))
        self.decide(ctx,'plan',assignments=[dict(unit_id=u['id'],description='help',expected='result.json')])
        intake=self.s.claim();self.decide(intake,'reject');ctx=self.s.claim()
        self.decide(ctx,'deliver',result='partner rejected; limited result',reviews=[dict(task_id=intake['task']['id'],verdict='waive',feedback='explicit limitation')])
        self.assertEqual(self.view(run)['status'],'partial');self.assertIsNone(self.view(run)['result'])
    def test_other_claim_context_cannot_publish_decision(self):
        run,ctx=self.start()
        with self.assertRaises(Forbidden):self.s.decide(self.ctx,ctx['task']['id'],ctx['task']['attempt_id'],dict(action='deliver',summary='fake',result='fake'))
    def test_cancelled_dynamic_work_cannot_deliver(self):
        run,ctx=self.start();self.client.post('/api/v1/runs/'+run['id']+'/cancel')
        with self.assertRaises(DomainError):self.decide(ctx,'deliver',result='late',files=[dict(path='result.json',content='{"value":1}')])
