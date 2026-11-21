"""Deterministic compile-time component expansion without instance services."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchpact.cli import main
from orchpact.kernel.components import expand_components
from orchpact.kernel.workflows import definition_digest

SCHEMA={'type':'object','additionalProperties':False,'required':['value'],'properties':{'value':{'type':'integer'}}}


def tool(path):
    return {'interface':'document.render','version':'1','input_schema':{'type':'object','additionalProperties':False,
        'required':['value'],'properties':{'value':{'type':'integer'}}},
        'output_schema':{'type':'object','required':['files'],'properties':{'files':{'type':'object','required':[path],
            'properties':{path:deepcopy(SCHEMA)}}}},'effect':'read','max_output_bytes':100000}


def component():
    return {'format':'orchpact-component','component_id':'document.generate','version':'1.0.0',
        'parameters_schema':{'type':'object','additionalProperties':False,'required':['template'],'properties':{'template':{'type':'string'}}},
        'inputs':{'facts':{'kind':'json','schema':deepcopy(SCHEMA)}},'outputs':{'result':{'node':'render','file':'result.json'}},
        'resources':{'models':{'reasoning':{'capabilities':['text','json']}},'tools':{'renderer':tool('result.json')}},
        'units':[{'key':'entry','name':'Renderer','purpose':'Render facts','model':'reasoning'}],
        'operation':{'unit':'entry','instruction':'Render facts','input_schema':{'type':'object'},'output_schema':deepcopy(SCHEMA),
            'output_file':'result.json','execution_plan':{'result_node':'render','max_repairs':1,'nodes':[
                {'key':'render','kind':'tool','unit':'entry','description':{'$param':'template'},'expected':'result.json',
                 'tool_ref':'renderer','arguments':{'value':{'$ref':'input','path':'facts.value'}},
                 'outputs':{'result.json':deepcopy(SCHEMA)}}]}}}


def instance(alias,source,path):
    return {'use':'document.generate@1.0.0','sha256':definition_digest(source),'with':{'template':alias+' template'},
        'inputs':{'facts':{'$ref':'json','node':'facts','file':'facts.json','path':''}},'ports':{path:'result'}}


def author(source):
    first=instance('deployment',source,'deployment.json');second=instance('operations',source,'operations.json')
    return {'format':'orchpact-component-workflow','id':'component-test','name':'Component test','version':'1.0.0',
        'components':{'deployment':first,'operations':second},
        'workflow':{'resources':{'models':{},'tools':{'source':tool('facts.json'),'finalizer':tool('result.json')}},
            'units':[{'key':'entry','name':'Entry','purpose':'Compose components'}],
            'operations':{'generate':{'unit':'entry','instruction':'Compose two components','input_schema':deepcopy(SCHEMA),
                'output_schema':deepcopy(SCHEMA),'output_file':'result.json','execution_plan':{'result_node':'final','nodes':[
                    {'key':'facts','kind':'tool','unit':'entry','description':'Facts','expected':'facts.json','tool_ref':'source',
                     'arguments':{'value':{'$ref':'input','path':'value'}},'outputs':{'facts.json':deepcopy(SCHEMA)}},
                    {'component':'deployment','depends_on':['facts'],'outputs':{'deployment.json':deepcopy(SCHEMA)}},
                    {'component':'operations','depends_on':['facts'],'outputs':{'operations.json':deepcopy(SCHEMA)}},
                    {'key':'final','kind':'tool','unit':'entry','description':'Final','expected':'result.json','tool_ref':'finalizer',
                     'depends_on':['deployment','operations'],'arguments':{'value':{'$ref':'json','node':'deployment','file':'deployment.json','path':'value'}},
                     'outputs':{'result.json':deepcopy(SCHEMA)}}]}}}}}


def wrapper(child,component_id,alias='child'):
    parent=deepcopy(child);parent['component_id']=component_id
    parent['components']={alias:instance(alias,child,'result.json')}
    parent['components'][alias]['use']=child['component_id']+'@'+child['version']
    parent['resources']={'models':{'reasoning':{'capabilities':['text','json']}},'tools':{}}
    parent['operation']['execution_plan']={'result_node':alias,'nodes':[{'component':alias,'outputs':{'result.json':deepcopy(SCHEMA)}}]}
    parent['outputs']={'result':{'node':alias,'file':'result.json'}}
    return parent


def single_host(source,use):
    host=author(source);host['components']={'deployment':instance('deployment',source,'deployment.json')}
    host['components']['deployment']['use']=use
    host['workflow']['operations']['generate']['execution_plan']['nodes']=[host['workflow']['operations']['generate']['execution_plan']['nodes'][0],
        {'component':'deployment','depends_on':['facts'],'outputs':{'deployment.json':deepcopy(SCHEMA)}}]
    host['workflow']['operations']['generate']['execution_plan']['result_node']='deployment'
    host['workflow']['operations']['generate']['output_file']='deployment.json'
    return host


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=component()
        (self.root/'document.generate@1.0.0.json').write_text(json.dumps(self.source))

    def tearDown(self):self.tmp.cleanup()

    def test_double_instance_namespaces_ports_and_deterministic_report(self):
        definition,report=expand_components(author(self.source),self.root)
        self.assertNotIn('components',definition)
        nodes=definition['operations']['generate']['execution_plan']['nodes']
        keys={node['key'] for node in nodes}
        self.assertIn('deployment__render',keys);self.assertIn('operations__render',keys)
        self.assertIn('deployment__renderer',definition['resources']['tools'])
        self.assertIn('operations__renderer',definition['resources']['tools'])
        deployment=next(node for node in nodes if node['key']=='deployment__render')
        self.assertEqual(deployment['tool_ref'],'deployment__renderer')
        self.assertEqual(deployment['description'],'deployment template')
        self.assertEqual(deployment['arguments']['value'],{'$ref':'json','node':'facts','file':'facts.json','path':'value'})
        self.assertIn('deployment.json',deployment['outputs'])
        final=next(node for node in nodes if node['key']=='final')
        self.assertEqual(final['depends_on'],['deployment__render','operations__render'])
        self.assertEqual(final['arguments']['value']['node'],'deployment__render')
        self.assertEqual(report['definition_sha256'],definition_digest(definition))
        self.assertEqual(len(report['components']),2)
        self.assertTrue(all(row['instance'] in ('deployment','operations') and row['source_node']=='render' for row in report['source_map']))
        (self.root/'document.generate@1.0.0.json').write_text(json.dumps(self.source,ensure_ascii=False,indent=2))
        again=expand_components(author(self.source),self.root)
        self.assertEqual(definition,again[0]);self.assertEqual(report['definition_sha256'],again[1]['definition_sha256'])

    def test_nested_component_and_cycle_rejection(self):
        child=self.source;parent=wrapper(child,'document.parent')
        (self.root/'document.parent@1.0.0.json').write_text(json.dumps(parent))
        host=author(child);host['components']={'deployment':instance('deployment',parent,'deployment.json')}
        host['components']['deployment']['use']='document.parent@1.0.0'
        host['workflow']['operations']['generate']['execution_plan']['nodes']=[host['workflow']['operations']['generate']['execution_plan']['nodes'][0],
            {'component':'deployment','depends_on':['facts'],'outputs':{'deployment.json':deepcopy(SCHEMA)}}]
        host['workflow']['operations']['generate']['execution_plan']['result_node']='deployment'
        host['workflow']['operations']['generate']['output_file']='deployment.json'
        definition,report=expand_components(host,self.root)
        self.assertIn('deployment__child__renderer',definition['resources']['tools'])
        self.assertEqual([(row['node'],row['instance'],row['source_node']) for row in report['source_map']],
            [('deployment__child__render','deployment__child','render')])
        a=wrapper(child,'document.a','b');b=wrapper(child,'document.b','a')
        b['components']['a']['use']='document.a@1.0.0';b['components']['a']['sha256']=definition_digest(a)
        a['components']['b']['use']='document.b@1.0.0';a['components']['b']['sha256']=definition_digest(b)
        (self.root/'document.a@1.0.0.json').write_text(json.dumps(a));(self.root/'document.b@1.0.0.json').write_text(json.dumps(b))
        cyclic=author(a);cyclic['components']['deployment']['use']='document.a@1.0.0'
        with self.assertRaisesRegex(ValueError,'循环引用'):expand_components(cyclic,self.root)

    def test_invalid_parameters_references_digests_and_contracts_are_rejected(self):
        cases=[]
        value=author(self.source);value['components']['deployment']['sha256']='0'*64;cases.append(value)
        value=author(self.source);value['components']['deployment']['with']={};cases.append(value)
        value=author(self.source);value['components']['deployment']['with']['extra']=True;cases.append(value)
        value=author(self.source);value['components']['deployment']['use']='document.generate@2.0.0';cases.append(value)
        value=author(self.source);value['components']['deployment']['inputs']['facts']['$ref']='input';cases.append(value)
        value=author(self.source);value['workflow']['operations']['generate']['execution_plan']['nodes'][1]['outputs']['deployment.json']['type']='number';cases.append(value)
        value=author(self.source);value['workflow']['operations']['generate']['execution_plan']['nodes'][1]['unexpected']='x';cases.append(value)
        value=author(self.source);value['components']['deployment']['with']['template']=1;cases.append(value)
        value=author(self.source);value['workflow']['operations']['generate']['execution_plan']['nodes'][1]['artifact_outputs']={'deployment.json':{'media_type':'application/json','max_bytes':100}};cases.append(value)
        for invalid in cases:
            with self.subTest(case=invalid),self.assertRaises(ValueError):expand_components(invalid,self.root)

    def test_artifact_port_and_verifier_are_rewritten(self):
        source=deepcopy(self.source);node=source['operation']['execution_plan']['nodes'][0]
        node.update(outputs={},artifact_outputs={'result.pdf':{'media_type':'application/pdf','max_bytes':128}},expected='result.pdf',
            verification={'tool_ref':'verifier','required_checks':['structure'],'parameters':{'strict':True}})
        source['resources']['tools']['renderer']={'interface':'document.render','version':'1','input_schema':tool('result.pdf')['input_schema'],
            'output_schema':{'type':'object','required':['artifacts'],'properties':{'artifacts':{'type':'object','required':['result.pdf'],
                'properties':{'result.pdf':{'type':'object'}}}}},'effect':'create_artifact',
            'artifact_contract':{'media_types':['application/pdf'],'max_files':1,'max_bytes':128},'max_output_bytes':100000}
        source['resources']['tools']['verifier']={'interface':'document.verify','version':'1','input_schema':{'type':'object'},
            'output_schema':{'type':'object','required':['status','checks'],'properties':{'status':{'type':'string'},'checks':{'type':'array'}}},'effect':'read','max_output_bytes':100000}
        source['outputs']['result']['file']='result.pdf'
        source['operation'].pop('output_schema');source['operation'].update(output_kind='artifact',output_file='result.pdf',
            artifact_output={'media_type':'application/pdf','max_bytes':128})
        (self.root/'document.generate@1.0.0.json').write_text(json.dumps(source))
        host=single_host(source,'document.generate@1.0.0');host['components']['deployment']['ports']={'deployment.pdf':'result'}
        placeholder=host['workflow']['operations']['generate']['execution_plan']['nodes'][1]
        placeholder.pop('outputs');placeholder['artifact_outputs']={'deployment.pdf':{'media_type':'application/pdf','max_bytes':128}}
        operation=host['workflow']['operations']['generate'];operation.pop('output_schema')
        operation.update(output_kind='artifact',output_file='deployment.pdf',artifact_output={'media_type':'application/pdf','max_bytes':128})
        definition,_=expand_components(host,self.root);render=definition['operations']['generate']['execution_plan']['nodes'][1]
        self.assertEqual(render['verification']['tool_ref'],'deployment__verifier')
        self.assertEqual(set(render['artifact_outputs']),{'deployment.pdf'})
        renderer=definition['resources']['tools']['deployment__renderer']
        self.assertEqual(renderer['output_schema']['properties']['artifacts']['required'],['deployment.pdf'])

    def test_group_exports_follow_the_output_port(self):
        source=deepcopy(self.source);plan=source['operation']['execution_plan'];render=plan['nodes'][0]
        render['parent']='bundle';plan['nodes'].append({'key':'bundle','kind':'group','unit':'entry','description':'Bundle',
            'expected':'result.json','exports':['result.json']})
        (self.root/'document.generate@1.0.0.json').write_text(json.dumps(source))
        definition,_=expand_components(single_host(source,'document.generate@1.0.0'),self.root)
        nodes=definition['operations']['generate']['execution_plan']['nodes']
        group=next(node for node in nodes if node['key']=='deployment__bundle')
        self.assertEqual(group['exports'],['deployment.json'])
        self.assertEqual(definition['operations']['generate']['execution_plan']['result_node'],'deployment__render')

    def test_depth_instance_node_size_and_namespace_limits(self):
        current=self.source
        for level in range(5):
            current=wrapper(current,'document.level'+str(level));use=current['component_id']+'@1.0.0'
            (self.root/(use+'.json')).write_text(json.dumps(current))
        with self.assertRaisesRegex(ValueError,'超过4层'):expand_components(single_host(current,use),self.root)
        host=author(self.source);host['components']={};nodes=[host['workflow']['operations']['generate']['execution_plan']['nodes'][0]]
        for index in range(33):
            alias='component'+str(index);host['components'][alias]=instance(alias,self.source,alias+'.json')
            nodes.append({'component':alias,'depends_on':['facts'],'outputs':{alias+'.json':deepcopy(SCHEMA)}})
        operation=host['workflow']['operations']['generate'];operation['execution_plan'].update(result_node='component0',nodes=nodes);operation['output_file']='component0.json'
        with self.assertRaisesRegex(ValueError,'超过32'):expand_components(host,self.root)
        large=deepcopy(self.source);template=large['operation']['execution_plan']['nodes'][0];large['operation']['execution_plan']['nodes']=[]
        for index in range(40):
            node=deepcopy(template);node['key']='node'+str(index);large['operation']['execution_plan']['nodes'].append(node)
        large['operation']['execution_plan']['result_node']='node39';large['outputs']['result']['node']='node39'
        (self.root/'document.generate@1.0.0.json').write_text(json.dumps(large))
        with self.assertRaises(ValueError):expand_components(single_host(large,'document.generate@1.0.0'),self.root)
        (self.root/'document.generate@1.0.0.json').write_text(json.dumps(self.source))
        oversized=single_host(self.source,'document.generate@1.0.0');oversized['description']='x'*1500000
        with self.assertRaises(ValueError):expand_components(oversized,self.root)
        duplicate=author(self.source);duplicate['workflow']['operations']['again']=deepcopy(duplicate['workflow']['operations']['generate'])
        with self.assertRaisesRegex(ValueError,'命名空间冲突'):expand_components(duplicate,self.root)

    def test_cli_is_offline_and_never_overwrites_outputs(self):
        source_path=self.root/'author.json';source_path.write_text(json.dumps(author(self.source)))
        output=self.root/'expanded.json';report=self.root/'report.json'
        argv=['orchpact','component-expand',str(source_path),'--components-dir',str(self.root),'--output',str(output),'--report',str(report)]
        with patch('sys.argv',argv),patch('orchpact.cli.Settings.load',side_effect=AssertionError('instance config loaded')),patch('sys.stdout',new_callable=io.StringIO):
            main()
        expanded=json.loads(output.read_text());metadata=json.loads(report.read_text())
        self.assertNotIn('components',expanded);self.assertIn('source_map',metadata)
        with patch('sys.argv',argv),self.assertRaises(SystemExit):main()


if __name__=='__main__':unittest.main()
