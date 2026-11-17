"""Real worker threads and PostgreSQL admission with no external providers."""
import threading
from dataclasses import replace
from runtime_fixtures import KernelFixture,package
from orchpact.services.runner import Runner
from orchpact.services.executor import CrewAIExecutor

class BlockingExecutor:
    def __init__(self,service):
        self.service=service;self.condition=threading.Condition();self.release=threading.Event();self.entered=[];self.finished=0
    def execute(self,ctx):
        with self.condition:self.entered.append(ctx['task']);self.condition.notify_all()
        if not self.release.wait(10):raise RuntimeError('fixture deadline')
        self.service.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'fixture',[dict(path='result.json',content='{"value":1}')])
        with self.condition:self.finished+=1;self.condition.notify_all()
    def wait_for(self,predicate):
        with self.condition:return self.condition.wait_for(predicate,10)

class ConcurrencyTests(KernelFixture):
    def test_workers_overlap_distinct_projects_and_finish_once(self):
        self.register()
        for index in range(4):self.invoke(False,'run-'+str(index))
        executor=BlockingExecutor(self.s);runner=Runner(self.s,executor)
        try:
            runner.start();self.assertTrue(executor.wait_for(lambda:len(executor.entered)==2));self.assertTrue(runner.healthy())
            self.assertEqual(len({t['project_id'] for t in executor.entered}),2)
            executor.release.set();self.assertTrue(executor.wait_for(lambda:executor.finished==4))
        finally:executor.release.set();runner.stop()
        self.assertEqual(len({t['id'] for t in executor.entered}),4)
        self.assertTrue(all(r['status']=='completed' for r in self.s.list_invocations(self.ctx,'fixture')))
    def test_project_parallelism_is_bounded_and_configurable(self):
        value=package();node=dict(value['operations']['generate']['execution_plan']['nodes'][0]);node['key']='other';value['operations']['generate']['execution_plan']['nodes'].append(node)
        self.invoke(value);first=self.s.claim();self.assertIsNotNone(first);self.assertIsNone(self.s.claim())
        self.s.settings=replace(self.settings,max_tasks_per_project=2);self.assertIsNotNone(self.s.claim());self.assertIsNone(self.s.claim())
    def test_stopped_runner_does_not_claim(self):
        self.invoke();runner=Runner(self.s);runner.stop();self.assertFalse(runner.step())
    def test_missing_thread_fails_readiness(self):
        runner=Runner(self.s);self.assertFalse(runner.healthy())
