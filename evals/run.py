"""Compare three organization modes on one kernel. Scripted results are plumbing only."""
import argparse
from copy import deepcopy
import json
import secrets
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from orchpact.testing import database_url_for
from orchpact.bootstrap import Instance
from orchpact.config import Settings
from orchpact.kernel.models import ExecutionContext
from orchpact.adapters.providers import Providers
from orchpact.services.executor import CrewAIExecutor

ROOT=Path(__file__).resolve().parents[1]

class Scripted(Providers):
    def json(self,ctx,prompt,inputs,schema):
        request=self.reserve(ctx,'model',ctx['snapshot']['profile']['id'],token_bound=1000)
        self.record(request,'completed',{'total_tokens':1000},'synthetic-fixture')
        original=inputs['application']['input']['records']
        result=dict(total=sum(r['amount'] for r in original),source_ids=[r['id'] for r in original])
        node=ctx.get('planned_node')
        if node:
            if node['gate']:return dict(files={'audit.json':dict(passed=True,issues=[],reviewed_artifact_ids=[d['artifact_id'] for d in ctx['deliveries']])})
            return dict(files={'result.json':result})
        if ctx['task']['kind']=='work':return dict(summary='synthetic calculation',files=[dict(path='result.json',content=json.dumps(result))])
        if not ctx['children']:
            return dict(action='plan',summary='synthetic delegation',recruits=[dict(name='Analyst',role='Analyst',reason='calculate',capabilities=['text','json'])],assignments=[dict(agent='Analyst',description='Calculate from original records',expected='result.json')])
        child=ctx['children'][0]
        return dict(action='deliver',summary='synthetic acceptance',result='Synthetic fixture result',source_artifact_id=child['artifact_id'],reviews=[dict(task_id=child['id'],verdict='accept',feedback='Synthetic fixture verification')])

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport',choices=['scripted','live'],default='scripted')
    parser.add_argument('--mode',choices=['single','fixed','autonomous','all'],default='all')
    parser.add_argument('--root',type=Path,help='Explicit model configuration for live calls')
    parser.add_argument('--budget-points',type=int,default=100)
    parser.add_argument('--repetitions',type=int,default=1)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.repetitions<=20 or not 1<=args.budget_points<=100000:parser.error('Invalid repetition/budget bound')
    if args.transport=='live' and not args.root:parser.error('Live calls require --root and explicit budget')
    if args.output.exists():parser.error('Output already exists; preserve previous results')
    source=Settings.load(args.root) if args.transport=='live' else None
    if source:
        profiles=deepcopy(source.active_profiles())
        credentials={key:source._configuration.secret(profile.pop('credential')) for key,profile in profiles.items()}
    else:
        profiles={'fixture':dict(id='fixture',kind='text',model='synthetic',base_url='https://example.invalid',capabilities=['text','json'])}
        credentials={'fixture':'synthetic-eval-key'}
    cases=json.loads((ROOT/'evals/cases.json').read_text());rows=[]
    modes=['single','fixed','autonomous'] if args.mode=='all' else [args.mode]
    # A separate fresh instance per mode gives all modes the same finite wallet.
    for mode in modes:
        with tempfile.TemporaryDirectory(prefix='orchpact-eval-') as directory:
            path=Path(directory)
            settings=Settings(path,profiles,path/'var',workers=1,database_url=database_url_for(path/'var'))
            instance=Instance(settings).open()
            try:
                s=instance.service;actor=instance.service.access.setup_admin('scenario-owner',secrets.token_urlsafe(48),offline=True)
                config=s.configuration.public()
                for row in config['value']['providers']:
                    if row['id'] in credentials:row['secret']=credentials[row['id']]
                s.configuration.save(config['revision'],config['value'],'evaluation')
                ctx=ExecutionContext('user:'+actor.user_id,actor.workspace_id,user_id=actor.user_id,admin=True)
                s.access.wallets.adjust(actor.user_id,args.budget_points,'explicit evaluation budget','eval',actor.user_id)
                with s.db.tx() as c:s.policies.save(c,'platform',0,dict(budget_points=args.budget_points))
                manifest=json.loads((ROOT/'examples/evidence-team.json').read_text())
                if mode=='single':manifest['operations']['calculate']['execution_plan']['nodes']=manifest['operations']['calculate']['execution_plan']['nodes'][:1]
                if mode=='autonomous':manifest['operations']['calculate'].pop('execution_plan')
                s.register_package(ctx,manifest)
                executor=CrewAIExecutor(s,Scripted(s) if args.transport=='scripted' else Providers(s))
                for repetition in range(args.repetitions):
                    for case in cases:
                        started=time.monotonic()
                        try:
                            run=s.invoke(ctx,manifest['id'],dict(operation='calculate',input=case['input'],context={}),f"{repetition}-{case['id']}")
                        except (ValueError,KeyError) as exc:
                            rows.append(dict(case=case['id'],mode=mode,repetition=repetition,transport=args.transport,quality_evidence=args.transport=='live',passed=False,status='admission_rejected',error_type=type(exc).__name__,known_points=0,usage_complete=True,requests=0,attempts=0,seconds=round(time.monotonic()-started,4),output=None))
                            continue
                        for _ in range(96):
                            claimed=s.claim()
                            if not claimed:break
                            try:executor.execute(claimed)
                            except Exception as exc:
                                s.fail(claimed['execution_context'],claimed['task']['id'],claimed['task']['attempt_id'],type(exc).__name__)
                                break
                        result=s.invocation(ctx,manifest['id'],run['id']);output=result['result']
                        passed=isinstance(output,dict) and output.get('total')==case['expected']['total'] and set(output.get('source_ids',[]))==set(case['expected']['source_ids'])
                        rows.append(dict(case=case['id'],mode=mode,repetition=repetition,transport=args.transport,quality_evidence=args.transport=='live',passed=passed,status=result['status'],known_points=result['usage']['points'],usage_complete=result['usage']['complete'],requests=len(result['requests']),attempts=sum(t['turns'] for t in result['tasks']),seconds=round(time.monotonic()-started,4),output=output))
            finally:instance.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(format='orchpact-eval-v1',transport=args.transport,synthetic=args.transport=='scripted',budget_per_mode=args.budget_points,models=[dict(id=p['id'],model=p['model']) for p in profiles.values()],results=rows),ensure_ascii=False,indent=2)+'\n')
    print(f'{len(rows)} runs; {sum(r["passed"] for r in rows)} passed; transport={args.transport}; synthetic results do not measure model quality.')
    if not all(r['passed'] for r in rows):raise SystemExit(1)

if __name__=='__main__':main()
