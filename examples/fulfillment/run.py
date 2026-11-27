"""Eight-stage fulfillment acceptance exercise. Synthetic by default; live is opt-in."""
import argparse
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import secrets
import tempfile
from unittest.mock import patch

import httpx

from orchpact.adapters.tools import ToolRegistry
from orchpact.bootstrap import Instance
from orchpact.config import Settings
from orchpact.kernel.models import ExecutionContext
from orchpact.services.tools import ToolRuntime
from orchpact.testing import database_url_for
from orchpact.scenarios.fulfillment import check_plan, copy_stage, summary, FulfillmentVerifier

HERE=Path(__file__).resolve().parent
HttpClient=httpx.Client
STAGES=['demand','stock','routes','allocation','costs','service','audit','release']
CASES=['pass','repair-stock','repair-budget','reject-release','inconclusive']


def execute(case,transport='synthetic',live_root=None,model_id=None,budget_points=1000):
    original=json.loads((HERE/'input.json').read_text())
    if case=='inconclusive':original['rate_confirmation']='pending'
    definition=json.loads((HERE/'workflow.json').read_text())
    if transport=='live':
        source=Settings.load(live_root)
        profile=deepcopy(source.choose(['text','json'],model_id))
        secret=source._configuration.secret(profile.pop('credential'))
    else:
        profile=dict(id='demo',label='Synthetic fulfillment',kind='text',model='synthetic',
                     base_url='https://example.invalid/v1',capabilities=['text','json'],priority=1,json_mode=True)
        secret='synthetic-fixture-key'
    calls=Counter();initial={};first_attempts={};fault_seen=False
    with ExitStack() as stack:
        directory=stack.enter_context(tempfile.TemporaryDirectory(prefix='orchpact-fulfillment-'))
        root=Path(directory)
        settings=Settings(root,{profile['id']:profile},root/'var',database_url=database_url_for(root/'var'))
        instance=Instance(settings).open()
        try:
            service=instance.service
            actor=instance.service.access.setup_admin('scenario-owner',secrets.token_urlsafe(48),offline=True)
            ctx=ExecutionContext('user:'+actor.user_id,actor.workspace_id,user_id=actor.user_id,admin=True)
            service.access.wallets.adjust(ctx.user_id,budget_points,'Explicit isolated fulfillment budget','fulfillment-budget',ctx.user_id)
            with service.db.tx() as c:service.policies.save(c,'platform',0,dict(budget_points=budget_points))
            config=service.configuration.public();provider=deepcopy(config['value']['providers'][0])
            config['value']['providers'][0]['secret']=secret
            provider.update(id='tool:fulfillment-verifier',category='tool',label='Fulfillment verifier',adapter='builtin',base_url='',model='',search=False,thinking_disabled=False,image_options={},
                pricing=dict(unit='request',points=0,unit_size=1),tool_interface='example.fulfillment.verify',tool_interface_version='1',implementation_version='1.0.0',
                tool_policy=dict(workspace_ids=[ctx.workspace_id],workflow_ids=[definition['id']],max_concurrency=1),
                tool_verifier=dict(checks=STAGES,media_types=['application/json'],trust_level='trusted',environment='isolated-fulfillment-python-v1'))
            service.configuration.save(config['revision'],dict(config['value'],providers=config['value']['providers']+[provider]),ctx.subject)
            service.tools=ToolRuntime(service,ToolRegistry(builtins={'example.fulfillment.verify':FulfillmentVerifier(service)}))
            service.workflows.register(ctx,definition)
            service.delivery.install(ctx,definition['id'],1,dict(models={'reasoning':profile['id']},tools={'verifier':dict(endpoint_ref=provider['id'],implementation_version='1.0.0')}),0)
            def send(request):
                if request.url.host!='example.invalid':raise AssertionError('Unexpected network target')
                inputs=json.loads(json.loads(request.content)['messages'][1]['content']);stage=inputs['stage_contract']['key'];calls[stage]+=1
                docs={}
                for delivery in inputs['deliveries']:
                    for file in delivery['files']:docs[file['path']]=json.loads(file['content'])
                if stage in ('demand','stock','routes'):candidate=copy_stage(original,stage)
                elif stage=='allocation':
                    candidate=json.loads((HERE/'feasible-plan.json').read_text())
                    if case=='repair-stock' and calls[stage]==1:
                        next(row for row in candidate['shipments'] if row['order_id']=='O4' and row['sku']=='A')['ship_day']=1
                    if case=='repair-budget' and calls[stage]==1:candidate['shipments'][0]['carrier']='express'
                elif stage in ('costs','service'):candidate=summary(original,docs['allocation.json'],stage)
                elif stage=='audit':candidate=dict(passed=True,issues=[],reviewed_artifact_ids=[row['artifact_id'] for row in inputs['inputs']])
                else:
                    candidate=dict(shipments=docs['allocation.json']['shipments'],total_cost_cents=docs['costs.json']['total_cost_cents'],deliveries=docs['service.json']['deliveries'],dependency_artifact_ids=[row['artifact_id'] for row in inputs['inputs']])
                    if case=='reject-release':
                        if calls[stage]==1:candidate['total_cost_cents']=0
                        elif calls[stage]==2:candidate['dependency_artifact_ids'][0]='stale-artifact'
                        else:
                            next(row for row in candidate['shipments'] if row['order_id']=='O7' and row['sku']=='A')['ship_day']=3
                return httpx.Response(200,json=dict(id='synthetic-fulfillment',choices=[dict(finish_reason='stop',message=dict(content=json.dumps({'files':{stage+'.json':candidate}})))],usage={'total_tokens':10}))
            if transport=='synthetic':stack.enter_context(patch('orchpact.adapters.providers.httpx.Client',side_effect=lambda **kw:HttpClient(transport=httpx.MockTransport(send),**kw)))
            run=service.delivery.invoke(ctx,definition['id'],dict(operation='fulfill',input=original,context={}), 'fulfillment-run')
            for _ in range(96):
                progressed=instance.runner.step()
                view=service.delivery.run(ctx,run['id'])
                tasks={row['node_key']:row for row in view['tasks'] if row['node_key']}
                for stage in ('demand','stock','routes'):
                    if stage in tasks and tasks[stage]['artifact_id']:
                        initial.setdefault(stage,tasks[stage]['artifact_id']);first_attempts.setdefault(stage,tasks[stage]['turns'])
                fault_seen=fault_seen or any(row['status']=='FAIL' for row in view['verification_evidence'])
                if not progressed:break
            accepted=view['status']=='completed'
            if accepted:
                checked=check_plan(original,{'shipments':view['result']['shipments']})
                if checked['status']!='PASS' or view['result']['total_cost_cents']!=checked['total_cost_cents']:raise AssertionError('False acceptance detected')
            preserved=set(initial)=={'demand','stock','routes'} and all(tasks[stage]['artifact_id']==artifact and tasks[stage]['turns']==first_attempts[stage] for stage,artifact in initial.items())
            expected_accept=case in ('pass','repair-stock','repair-budget')
            passed=accepted==expected_accept and (preserved or transport=='live')
            if case.startswith('repair-'):passed=passed and fault_seen and view['execution_plan']['repairs']==1
            if case=='reject-release':
                codes={row['checks'][0].get('code') for row in view['verification_evidence'] if row['status']=='FAIL'}
                passed=passed and fault_seen and view['execution_plan']['repairs']==2 and {'release_cost','release_evidence','changed_allocation'}<=codes
            if case=='inconclusive':passed=passed and view['execution_plan']['repairs']==0 and any(row['status']=='INCONCLUSIVE' and row['checks'][0]['check_id']=='allocation' and row['checks'][0].get('code')=='tariff_unconfirmed' for row in view['verification_evidence'])
            report=dict(case=case,passed=passed,accepted=accepted,status=view['status'],transport=transport,synthetic_model=transport=='synthetic',quality_evidence=transport=='live',
                repairs=view['execution_plan']['repairs'],independent_branches_preserved=preserved,requests=len(view['requests']),usage=view['usage'],result=view['result'],
                stages=[dict(key=key,status=task['status'],generation=task['generation'],attempts=task['turns'],artifact_id=task['artifact_id']) for key,task in tasks.items()],
                evidence=[dict(stage=row['checks'][0]['check_id'],status=row['status'],generation=row['generation'],artifact_id=row['artifact_id'],artifact_sha256=row['artifact_sha256'],contract_digest=row['contract_digest'],verifier_version=row['implementation_version'],tool_call_id=row['tool_call_id'],code=row['checks'][0].get('code')) for row in view['verification_evidence']],
                errors=view['blockers'],workflow=view['workflow'],model_id=profile['id'])
            return report
        finally:instance.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=CASES+['all'],default='all')
    parser.add_argument('--transport',choices=['synthetic','live'],default='synthetic')
    parser.add_argument('--live-root',type=Path)
    parser.add_argument('--model-id')
    parser.add_argument('--budget-points',type=int)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output and args.output.exists():parser.error('Output already exists; preserve prior evidence')
    if args.transport=='live' and (not args.live_root or not args.model_id or not args.budget_points or args.case not in ('pass','inconclusive')):
        parser.error('Live paid calls require --live-root, --model-id, --budget-points and --case pass|inconclusive')
    if args.budget_points is not None and not 1<=args.budget_points<=100000:parser.error('Budget must be between 1 and 100000 internal points')
    cases=CASES if args.case=='all' else [args.case]
    results=[execute(case,args.transport,args.live_root,args.model_id,args.budget_points or 1000) for case in cases]
    report=dict(format='orchpact-fulfillment-validation-v1',passed=all(row['passed'] for row in results),results=results)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x') as stream:stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['passed'] else 1)
