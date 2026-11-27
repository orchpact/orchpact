"""Synthetic artifact and verifier workflows on isolated PostgreSQL."""
import base64
from copy import deepcopy
from hashlib import sha256

from orchpact.adapters.tools import ToolRegistry
from orchpact.services.tools import ToolRuntime
from runtime_fixtures import KernelFixture, SCHEMA
from test_tool_runtime import SyntheticTool
from test_workflows import definition

PDF_MEDIA='application/pdf'
PDF_BYTES=b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n'
VERIFICATION={'tool_ref':'verifier','required_checks':['structure'],'parameters':{'strict':True}}


def artifact_contract(media=PDF_MEDIA,max_bytes=1000000):
    return {'media_type':media,'max_bytes':max_bytes}


def artifact_entry(data=PDF_BYTES,media=PDF_MEDIA,sha=None):
    return {'data_base64':base64.b64encode(data).decode(),'sha256':sha or sha256(data).hexdigest(),'media_type':media}


def renderer_result(context,data=PDF_BYTES,media=PDF_MEDIA):
    result={}
    outputs=context.definition['output_schema']['properties']
    if 'files' in outputs:
        result['files']={path:{'value':context.arguments['value']} for path in outputs['files']['required']}
    if 'artifacts' in outputs:
        result['artifacts']={path:artifact_entry(data,media) for path in outputs['artifacts']['required']}
    return result


def verifier_result(status='PASS',checks=None):
    return {'status':status,'checks':checks if checks is not None else [dict(check_id='structure',status=status,code=None,path='deployment.pdf',field_path=None,message=status,repair='')],
        'evidence':{'summary':'synthetic verification'}}


def workflow(node_verification=False,root_verification=False,root_artifact=True,mixed=False):
    value=definition();value['resources']={'tools':{
        'renderer':{'interface':'document.render','version':'1','description':'Synthetic artifact renderer',
            'input_schema':{'type':'object','additionalProperties':False,'required':['value'],'properties':{'value':{'type':'integer'}}},
            'output_schema':{'type':'object','additionalProperties':False,'required':['artifacts'],'properties':{
                'artifacts':{'type':'object','required':['deployment.pdf'],'properties':{'deployment.pdf':{'type':'object'}}}}},
            'effect':'create_artifact','artifact_contract':{'media_types':[PDF_MEDIA],'max_files':2,'max_bytes':1000000},
            'max_output_bytes':1000000},
        'verifier':{'interface':'document.verify','version':'1','description':'Synthetic trusted verifier',
            'input_schema':{'type':'object','required':['artifact','contract_digest','parameters'],'properties':{
                'artifact':{'type':'object'},'contract_digest':{'type':'string'},'parameters':{'type':'object'}}},
            'output_schema':{'type':'object','required':['status','checks'],'properties':{'status':{'type':'string'},'checks':{'type':'array'},'evidence':{'type':'object'}}},
            'effect':'read','max_output_bytes':1000000},
    }}
    for unit in value['units']:unit.pop('model',None)
    outputs={}
    artifacts={'deployment.pdf':artifact_contract()}
    if mixed:
        outputs={'result.json':deepcopy(SCHEMA)}
        value['resources']['tools']['renderer']['output_schema']['required'].append('files')
        value['resources']['tools']['renderer']['output_schema']['properties']['files']={'type':'object','required':['result.json'],'properties':{'result.json':deepcopy(SCHEMA)}}
        value['resources']['tools']['renderer']['artifact_contract']['media_types']=['application/json',PDF_MEDIA]
    node={'key':'render','kind':'tool','unit':'entry','description':'Render a PDF','expected':'deployment.pdf',
        'tool_ref':'renderer','arguments':{'value':{'$ref':'input','path':'value'}},'outputs':outputs,'artifact_outputs':artifacts}
    if node_verification:node['verification']=deepcopy(VERIFICATION)
    operation=value['operations']['generate']
    operation.update(input_schema=deepcopy(SCHEMA),tool_limit=8,execution_plan={'nodes':[node],'result_node':'render','max_repairs':2})
    if root_artifact:
        operation.pop('output_schema',None)
        operation.update(output_kind='artifact',output_file='deployment.pdf',artifact_output=artifact_contract())
    elif root_verification:
        raise ValueError('JSON root fixture requires mixed=True')
    if root_verification:operation['verification']=deepcopy(VERIFICATION)
    if mixed and not root_artifact:operation.update(output_file='result.json',output_schema=deepcopy(SCHEMA))
    return value


class VerificationFixture(KernelFixture):
    def setUp(self):
        super().setUp()
        self.configure_tools()
        self.text_profile=deepcopy(self.settings.profiles['text'])
        self.settings.profiles.clear()
        self.renderer=SyntheticTool(result=renderer_result)
        self.verifier=SyntheticTool(result=verifier_result())
        self.s.tools=ToolRuntime(self.s,ToolRegistry(builtins={'document.render':self.renderer,'document.verify':self.verifier}))

    def config(self):
        return self.client.get('/api/v1/admin/configuration').json()

    def save_config(self,body):
        body.pop('restart_required',None)
        response=self.client.put('/api/v1/admin/configuration',json=body)
        self.assertEqual(response.status_code,200,response.text)

    def configure_tools(self):
        body=self.config();text=next(p for p in body['value']['providers'] if p['id']=='text')
        if self.config()['revision']==0:text['secret']='text-fixture-secret'
        renderer=deepcopy(text)
        renderer.update(id='tool:renderer',category='tool',label='Renderer',adapter='builtin',base_url='',model='',
            search=False,thinking_disabled=False,image_options={},pricing={'unit':'request','points':1,'unit_size':1},
            tool_interface='document.render',tool_interface_version='1',implementation_version='1.0.0',
            tool_policy={'workspace_ids':[self.workspace],'application_ids':['fixture'],'require_credential':True,'max_concurrency':2},secret='tool-secret')
        verifier=deepcopy(renderer)
        verifier.update(id='tool:verifier',label='Verifier',tool_interface='document.verify',
            tool_verifier={'checks':['structure','references'],'media_types':['application/json',PDF_MEDIA],
                'trust_level':'trusted','environment':'synthetic-builtin-v1'})
        body['value']['providers']=[p for p in body['value']['providers'] if p['id'] not in ('tool:renderer','tool:verifier')]+[renderer,verifier]
        self.save_config(body)

    def update_verifier(self,**changes):
        body=self.config();provider=next(p for p in body['value']['providers'] if p['id']=='tool:verifier')
        provider.update(changes);self.save_config(body)

    def bindings(self):
        return {'models':{},'tools':{slot:{'endpoint_ref':'tool:'+slot,'implementation_version':'1.0.0'} for slot in ('renderer','verifier')}}

    def install(self,value):
        self.register(value)
        response=self.client.post('/api/v1/apps/fixture/install',json={'bindings':self.bindings()})
        self.assertEqual(response.status_code,200,response.text)

    def invoke(self,key='verification-run'):
        response=self.client.post('/api/v1/apps/fixture/runs',json={'operation':'generate','input':{'value':7},'context':{}},headers={'Idempotency-Key':key})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def retry(self,task_id,key='verification-retry'):
        response=self.client.post('/api/v1/work-items/'+task_id+'/retry',headers={'Idempotency-Key':key})
        self.assertEqual(response.status_code,200,response.text)
