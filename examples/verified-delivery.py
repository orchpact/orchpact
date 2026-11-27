"""Offline delivery examples: real storage/verification, synthetic model responses.

Requires the same isolated PostgreSQL setup as tests. Never loads an instance
configuration, connects to a model provider, or executes generated business code.
"""
import argparse
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import secrets
import sys
import tempfile
from unittest.mock import patch

import httpx

from orchpact.adapters.database import loads
from orchpact.adapters.tools import ToolRegistry
from orchpact.bootstrap import Instance
from orchpact.config import Settings
from orchpact.kernel.models import ExecutionContext
from orchpact.services.tools import ToolRuntime
from orchpact.testing import database_url_for

HttpClient=httpx.Client
ROOT=Path(__file__).resolve().parents[1]


class TotalVerifier:
    def __init__(self,service,scenario):self.s=service;self.scenario=scenario

    def invoke(self,context):
        ref=context.arguments['artifact']
        with self.s.db.connect() as c:
            artifact=self.s.row(c,'artifacts',ref['artifact_id'])
            original=loads(c.execute('SELECT input FROM invocations WHERE id=%s',(artifact['project_id'],)).fetchone()[0])
        if ref['sha256']!=artifact['sha256']:raise ValueError('Candidate digest mismatch')
        files=self.s.artifacts.read_files(artifact)
        value=json.loads(next(file['data'] for file in files if file['path']=='result.json'))
        expected=sum(row['amount'] for row in original['records'])
        status='INCONCLUSIVE' if self.scenario=='inconclusive' else 'PASS' if value['total']==expected else 'FAIL'
        return dict(status=status,checks=[dict(check_id='total',status=status,code='total_matches_original_input',
            field_path='total',message=f'Expected {expected}; candidate {value["total"]}',repair='Recalculate from the original records.')],
            evidence=dict(expected=expected,actual=value['total'],method='deterministic integer sum'))


def workflow():
    schema=dict(type='object',additionalProperties=False,required=['total'],properties={'total':{'type':'integer'}})
    value=dict(format='orchpact-workflow',id='verified-total',name='Verified record total',version='1.0.0',
        resources=dict(models={'reasoning':{'capabilities':['text','json']}},tools={'verifier':dict(interface='demo.verify-total',version='1',
            input_schema=dict(type='object',required=['artifact','contract_digest','parameters']),
            output_schema=dict(type='object',required=['status','checks']),effect='read')}),
        units=[dict(key='entry',name='Calculation',purpose='Calculate the original record total',model='reasoning')],
        operations={'calculate':dict(unit='entry',instruction='Sum every amount in records.',
            input_schema=dict(type='object',required=['records'],properties={'records':dict(type='array',minItems=1,items=dict(type='object',required=['amount'],properties={'amount':{'type':'integer'}}))}),
            output_schema=schema,output_file='result.json',tool_limit=4,execution_plan=dict(result_node='calculate',max_repairs=1,nodes=[
                dict(key='calculate',unit='entry',description='Calculate the total',expected='result.json',
                    staff=dict(name='Calculator',role='Calculate',reason='Produce candidate',capabilities=['text','json'],model='reasoning'),outputs={'result.json':schema},
                    verification=dict(tool_ref='verifier',required_checks=['total'],parameters={}))]))})
    return value


def scenario(name):
    with tempfile.TemporaryDirectory(prefix='orchpact-delivery-demo-') as directory:
        root=Path(directory)
        profile=dict(id='demo',label='Synthetic demo',kind='text',model='synthetic',base_url='https://example.invalid/v1',capabilities=['text','json'],priority=1,json_mode=True)
        settings=Settings(root,{'demo':profile},root/'var',database_url=database_url_for(root/'var'))
        instance=Instance(settings).open()
        try:
            service=instance.service
            actor=instance.service.access.setup_admin('scenario-owner',secrets.token_urlsafe(48),offline=True)
            ctx=ExecutionContext('user:'+actor.user_id,actor.workspace_id,user_id=actor.user_id,admin=True)
            service.access.wallets.adjust(ctx.user_id,1000,'Synthetic isolated demo','demo-budget',ctx.user_id)
            config=service.configuration.public();provider=deepcopy(config['value']['providers'][0])
            provider.update(id='tool:demo-verifier',category='tool',label='Demo deterministic verifier',adapter='builtin',base_url='',model='',search=False,thinking_disabled=False,image_options={},
                pricing=dict(unit='request',points=0,unit_size=1),tool_interface='demo.verify-total',tool_interface_version='1',implementation_version='1.0.0',
                tool_policy=dict(workspace_ids=[ctx.workspace_id],max_concurrency=1),
                tool_verifier=dict(checks=['total'],media_types=['application/json'],trust_level='trusted',environment='isolated-demo-python'))
            config['value']['providers'][0]['secret']='synthetic-only'
            service.configuration.save(config['revision'],dict(config['value'],providers=config['value']['providers']+[provider]),ctx.subject)
            service.tools=ToolRuntime(service,ToolRegistry(builtins={'demo.verify-total':TotalVerifier(service,name)}))
            asset=service.workflows.register(ctx,workflow())
            bindings=dict(models={'reasoning':'demo'},tools={'verifier':dict(endpoint_ref='tool:demo-verifier',implementation_version='1.0.0')})
            service.delivery.install(ctx,asset['id'],1,bindings,0)
            calls=[]
            def send(request):
                if request.url.host!='example.invalid':raise AssertionError('Unexpected network target')
                inputs=json.loads(json.loads(request.content)['messages'][1]['content'])
                calls.append(inputs)
                total=sum(row['amount'] for row in inputs['application']['input']['records'])
                if name=='fail' or (name=='repair' and len(calls)==1):total+=1
                return httpx.Response(200,json=dict(id='synthetic-only',choices=[dict(finish_reason='stop',message=dict(content=json.dumps({'files':{'result.json':{'total':total}}})))],usage={'total_tokens':10}))
            run=service.delivery.invoke(ctx,asset['id'],dict(operation='calculate',input={'records':[{'amount':3},{'amount':5}]},context={}), 'demo-run')
            with ExitStack() as stack:
                stack.enter_context(patch('orchpact.adapters.providers.httpx.Client',side_effect=lambda **kw:HttpClient(transport=httpx.MockTransport(send),**kw)))
                for _ in range(8):
                    if not instance.runner.step():break
            view=service.delivery.run(ctx,run['id'])
            evidence=[dict(status=row['status'],artifact_id=row['artifact_id'],sha256=row['artifact_sha256'],generation=row['generation']) for row in view['verification_evidence']]
            accepted=view['status']=='completed'
            assert accepted==(name in ('pass','repair')),(name,view['status'])
            assert view['result']==({'total':8} if accepted else None)
            assert [row['status'] for row in evidence]=={'pass':['PASS'],'fail':['FAIL','FAIL'],'repair':['FAIL','PASS'],'inconclusive':['INCONCLUSIVE']}[name]
            return dict(scenario=name,accepted=accepted,status=view['status'],result=view['result'],model_calls=len(calls),repairs=view['execution_plan']['repairs'],evidence=evidence)
        finally:instance.close()


def run(output=None,selected='all'):
    if output and output.exists():raise FileExistsError('Preserve existing results; choose a new output path')
    names=['pass','fail','repair','inconclusive'] if selected=='all' else [selected]
    results=[scenario(name) for name in names]
    report=dict(format='orchpact-delivery-demo-v1',passed=True,synthetic_model=True,quality_evidence=False,storage='isolated-postgresql',results=results)
    if output:
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x') as stream:stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario',choices=['all','pass','fail','repair','inconclusive'],default='all')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    run(args.output,args.scenario)
