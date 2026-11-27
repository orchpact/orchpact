"""Creative delivery with declared-state checks, model review and human acceptance."""
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile
from unittest.mock import patch

import httpx
import psycopg

from orchpact.adapters.database import loads
from orchpact.adapters.tools import ToolRegistry
from orchpact.bootstrap import Instance
from orchpact.config import Settings
from orchpact.kernel.errors import Forbidden
from orchpact.kernel.models import ExecutionContext
from orchpact.services.tools import ToolRuntime
from orchpact.testing import database_url_for
from orchpact.scenarios.mystery import check_story, initial, StoryVerifier

HERE=Path(__file__).resolve().parent
HttpClient=httpx.Client
STAGES=['canon','structure','style','draft','editor','release']
CASES=['await-human','repair-knowledge','repair-branch','repair-editor','reject-clue','editor-inconclusive','annotation-blindspot']
EDITOR_DEFECT='E2.text: 周馆长被误写成守夜人；修正人物身份，与 canon 保持一致。'


def execute(case,simulate_approval=False,transport='synthetic',live_root=None,model_id=None,budget_points=1000,run_root=None):
    canon=json.loads((HERE/'input.json').read_text());definition=json.loads((HERE/'workflow.json').read_text())
    if transport=='live':
        source=Settings.load(live_root)
        profile=deepcopy(source.choose(['text','json'],model_id))
        secret=source._configuration.secret(profile.pop('credential'))
    else:
        profile=dict(id='demo',label='Synthetic mystery writer',kind='text',model='synthetic',
                     base_url='https://example.invalid/v1',capabilities=['text','json'],priority=1,json_mode=True)
        secret='synthetic-fixture-key'
    calls=Counter();preserved={}
    with ExitStack() as stack:
        if transport=='live':
            database_url=os.environ.get('ORCHPACT_SCENARIO_DATABASE_URL','')
            if not run_root or not database_url.startswith(('postgresql://','postgres://')):
                raise ValueError('Live runs require a new --run-root and dedicated empty ORCHPACT_SCENARIO_DATABASE_URL')
            if '\n' in database_url or '\r' in database_url:
                raise ValueError('Scenario database URL must be a single line')
            root=Path(run_root).resolve()
            if root.exists():raise FileExistsError('Live run root must be new; preserve existing evidence')
            try:
                with psycopg.connect(database_url,connect_timeout=5) as c:
                    populated=c.execute("SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname='public')").fetchone()[0]
            except psycopg.Error:raise RuntimeError('Cannot inspect the dedicated scenario database') from None
            if populated:raise ValueError('Live scenario database must be empty; existing databases are never reused')
            root.mkdir(mode=0o700,parents=True)
            env=root/'.env'
            with env.open('x') as stream:stream.write('ORCHPACT_DATABASE_URL='+database_url+'\n')
            env.chmod(0o600)
        else:
            directory=stack.enter_context(tempfile.TemporaryDirectory(prefix='orchpact-mystery-'));root=Path(directory)
            database_url=database_url_for(root/'var')
        instance=Instance(Settings(root,{profile['id']:profile},root/'var',database_url=database_url)).open()
        try:
            s=instance.service;actor=instance.service.access.setup_admin('scenario-owner',secrets.token_urlsafe(48),offline=True)
            ctx=ExecutionContext('user:'+actor.user_id,actor.workspace_id,user_id=actor.user_id,admin=True)
            s.access.wallets.adjust(ctx.user_id,budget_points,'Explicit isolated mystery budget','mystery-budget',ctx.user_id)
            with s.db.tx() as c:s.policies.save(c,'platform',0,dict(budget_points=budget_points))
            config=s.configuration.public();provider=deepcopy(config['value']['providers'][0])
            config['value']['providers'][0]['secret']=secret
            provider.update(id='tool:mystery-verifier',category='tool',label='Declared narrative state verifier',adapter='builtin',base_url='',model='',search=False,thinking_disabled=False,image_options={},pricing=dict(unit='request',points=0,unit_size=1),
                tool_interface='example.mystery.verify',tool_interface_version='1',implementation_version='1.0.0',tool_policy=dict(workspace_ids=[ctx.workspace_id],workflow_ids=[definition['id']],max_concurrency=1),
                tool_verifier=dict(checks=STAGES,media_types=['application/json'],trust_level='trusted',environment='isolated-narrative-state-v1'))
            s.configuration.save(config['revision'],dict(config['value'],providers=config['value']['providers']+[provider]),ctx.subject)
            s.tools=ToolRuntime(s,ToolRegistry(builtins={'example.mystery.verify':StoryVerifier(s)}))
            s.workflows.register(ctx,definition)
            s.delivery.install(ctx,definition['id'],1,dict(models={'reasoning':profile['id']},tools={'verifier':dict(endpoint_ref=provider['id'],implementation_version='1.0.0')}),0)
            with s.db.tx() as c:
                installed=s.delivery.installed(c,ctx,definition['id'])
                manifest=loads(c.execute('SELECT manifest FROM organization_packages WHERE workspace_id=%s AND application_id=%s',(ctx.workspace_id,installed['scope_id'])).fetchone()[0])
                s.policies.save(c,'unit:'+manifest['operations']['write']['unit_id'],0,dict(independent_review=True))
            def send(request):
                if request.url.host!='example.invalid':raise AssertionError('Unexpected network target')
                inputs=json.loads(json.loads(request.content)['messages'][1]['content']);stage=inputs['stage_contract']['key'];calls[stage]+=1
                docs={file['path']:json.loads(file['content']) for delivery in inputs['deliveries'] for file in delivery['files']}
                if stage in ('canon','structure','style'):value=initial(canon,stage)
                elif stage=='draft':
                    if case.startswith('repair-') and calls[stage]>1 and not inputs.get('repair_candidate',{}).get('complete'):
                        raise AssertionError('Writer did not receive its immutable previous candidate for repair')
                    value=json.loads((HERE/'reference-draft.json').read_text())
                    if case=='repair-knowledge' and calls[stage]==1:value['scenes'][1]['uses_secrets']=['K1']
                    if case=='repair-branch' and calls[stage]==1:value['transitions']=[edge for edge in value['transitions'] if edge['target']!='E2']
                    if case=='reject-clue':
                        for scene in value['scenes']:
                            if scene['id'].startswith('E'):scene['payoffs'].remove('C3')
                    if case=='annotation-blindspot':value['scenes'][0]['text']='林澈当场断定周馆长拼接录音掩盖调换展品的时间，所有秘密都已明白。'+value['scenes'][0]['text']
                    if case=='repair-editor':
                        if calls[stage]==1:value['scenes'][-1]['text']+='周馆长这位守夜人走向门口。'
                        elif EDITOR_DEFECT not in inputs['task']['description']:raise AssertionError('Specific editorial feedback did not reach the writer')
                elif stage=='editor':
                    if case=='repair-editor' and calls[stage]>1 and EDITOR_DEFECT in inputs['task']['description']:
                        raise AssertionError('Reviewer inherited a stale upstream repair requirement')
                    rejected=case=='repair-editor' and calls[stage]==1
                    value=dict(decision='REJECT' if rejected else 'INCONCLUSIVE' if case=='editor-inconclusive' else 'APPROVE',reviewed_artifact_ids=[row['artifact_id'] for row in inputs['inputs']],issues=[EDITOR_DEFECT] if rejected else [],assessment='Synthetic editor report; no real literary judgment has occurred.',
                        fact_checks=[dict(secret_id=secret['id'],ending_id=scene['id'],canonical_fact=secret['description'],quote=scene['text'],verdict='MATCH',reason='Synthetic judgment; no real semantic assessment') for secret in canon['secrets'] for scene in docs['draft.json']['scenes'] if scene['id'] in canon['endings']])
                else:value=dict(story=docs['draft.json'],dependency_artifact_ids=[row['artifact_id'] for row in inputs['inputs']])
                return httpx.Response(200,json=dict(id='synthetic-mystery',choices=[dict(finish_reason='stop',message=dict(content=json.dumps({'files':{stage+'.json':value}},ensure_ascii=False)))],usage={'total_tokens':10}))
            if transport=='synthetic':stack.enter_context(patch('orchpact.adapters.providers.httpx.Client',side_effect=lambda **kw:HttpClient(transport=httpx.MockTransport(send),**kw)))
            run=s.delivery.invoke(ctx,definition['id'],dict(operation='write',input=canon,context={}), 'mystery-run')
            for _ in range(96):
                progressed=instance.runner.step();view=s.delivery.run(ctx,run['id'])
                tasks={row['node_key']:row for row in view['tasks'] if row['node_key']}
                for stage in ('canon','structure','style'):
                    if tasks[stage]['artifact_id']:preserved.setdefault(stage,(tasks[stage]['artifact_id'],tasks[stage]['turns']))
                if not progressed:break
            root_task=next(row for row in view['tasks'] if row['parent_id'] is None)
            awaiting=root_task['status']=='completed' and root_task['review']=='pending' and root_task['requires_independent_review']
            self_accept_blocked=None
            if awaiting:
                with s.db.connect() as c:
                    root_row=s.row(c,'tasks',root_task['id']);artifact=s.row(c,'artifacts',root_task['artifact_id']);producer=s.task_context(c,root_row)
                command=dict(action='review',payload=dict(artifact_id=artifact['id'],sha256=artifact['sha256'],verdict='accept',feedback='Mechanism test'))
                try:s.command(producer,run['id'],dict(command,key='producer-self-accept'));self_accept_blocked=False
                except Forbidden:self_accept_blocked=True
                if simulate_approval:s.command(ctx,run['id'],dict(command,key='simulated-user-accept'))
            view=s.delivery.run(ctx,run['id']);accepted=view['status']=='completed'
            manuscript=None;candidate_source=None;editor_report=None
            if root_task['artifact_id']:
                with s.db.connect() as c:artifact=s.row(c,'artifacts',root_task['artifact_id'])
                manuscript=json.loads(s.artifacts.read_files(artifact)[0]['data'])['story']
                candidate_source='root_candidate'
            elif tasks['draft']['artifact_id']:
                with s.db.connect() as c:artifact=s.row(c,'artifacts',tasks['draft']['artifact_id'])
                manuscript=json.loads(s.artifacts.read_files(artifact)[0]['data']);candidate_source='draft_only'
            if tasks['editor']['artifact_id']:
                with s.db.connect() as c:artifact=s.row(c,'artifacts',tasks['editor']['artifact_id'])
                editor_report=json.loads(s.artifacts.read_files(artifact)[0]['data'])
            unchanged=len(preserved)==3 and all((tasks[key]['artifact_id'],tasks[key]['turns'])==value for key,value in preserved.items())
            passed=unchanged
            if case in ('await-human','repair-knowledge','repair-branch','repair-editor','annotation-blindspot'):
                passed=passed and awaiting and self_accept_blocked and accepted==simulate_approval
                if case.startswith('repair-'):passed=passed and view['execution_plan']['repairs']==1
                if case=='annotation-blindspot':passed=passed and check_story(canon,manuscript)['status']=='PASS'
            elif case=='reject-clue':passed=passed and not accepted and view['execution_plan']['repairs']==2 and any(row['checks'][0].get('code')=='unresolved_clue' for row in view['verification_evidence'])
            else:passed=passed and not accepted and view['execution_plan']['repairs']==0 and any(row['checks'][0].get('code')=='editor_uncertain' for row in view['verification_evidence'])
            return dict(case=case,passed=bool(passed),accepted=accepted,awaiting_human_before_simulation=awaiting,simulated_approval=simulate_approval,real_human_review=False,producer_self_accept_blocked=self_accept_blocked,
                transport=transport,run_root=str(root) if transport=='live' else None,quality_evidence=False,semantic_status='NOT_VERIFIED',repairs=view['execution_plan']['repairs'],independent_branches_preserved=unchanged,candidate_story=manuscript,candidate_source=candidate_source,editor_report=editor_report,
                stages=[dict(key=key,status=row['status'],generation=row['generation'],attempts=row['turns']) for key,row in tasks.items()],
                evidence=[dict(stage=row['checks'][0]['check_id'],status=row['status'],code=row['checks'][0].get('code'),generation=row['generation'],artifact_id=row['artifact_id'],sha256=row['artifact_sha256']) for row in view['verification_evidence']],usage=view['usage'],errors=view['blockers'])
        finally:instance.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=CASES+['all'],default='all')
    parser.add_argument('--simulate-approval',action='store_true')
    parser.add_argument('--transport',choices=['synthetic','live'],default='synthetic')
    parser.add_argument('--live-root',type=Path);parser.add_argument('--model-id');parser.add_argument('--budget-points',type=int)
    parser.add_argument('--run-root',type=Path,help='New persistent evidence directory for a live run')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output and args.output.exists():parser.error('Output exists; choose a new path')
    if args.transport=='live' and (not args.live_root or not args.run_root or not os.environ.get('ORCHPACT_SCENARIO_DATABASE_URL') or not args.model_id or not args.budget_points or args.case!='await-human' or args.simulate_approval):parser.error('Paid live mode requires --case await-human, --live-root, --run-root, --model-id, --budget-points and a dedicated ORCHPACT_SCENARIO_DATABASE_URL; simulated approval is unavailable')
    if args.transport=='synthetic' and args.run_root:parser.error('--run-root is reserved for persistent live evidence')
    if args.simulate_approval and (args.transport!='synthetic' or args.case not in ('await-human','repair-knowledge','repair-branch')):parser.error('Simulated approval is only a mechanism check for a specified structurally valid synthetic case')
    if args.budget_points is not None and not 1<=args.budget_points<=100000:parser.error('Budget out of range')
    rows=[execute(case,args.simulate_approval,args.transport,args.live_root,args.model_id,args.budget_points or 1000,args.run_root) for case in CASES if args.case in ('all',case)]
    report=dict(format='orchpact-mystery-validation-v1',passed=all(row['passed'] for row in rows),results=rows)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x') as stream:stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['passed'] else 1)
