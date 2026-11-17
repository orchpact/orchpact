"""Joint citation feedback preserves the frozen contract and source material."""
import copy
import json
import unittest
from orchpact.reference_feedback import contracts, selection_outputs, authoring_node
from orchpact.planned import output_model


class ReferenceFeedbackTests(unittest.TestCase):
    def test_reports_missing_references_and_budget_together_without_mutation(self):
        node={'outputs':{'result.json':{'properties':{'sources':{'maxItems':12}}}},'rules':[
            dict(kind='references',file='result.json',path='findings.*.source_ids.*',target_path='sources.*.id'),
            dict(kind='text_references',file='result.json',paths=['body'],target_path='sources.*.id',prefix='S')]}
        sources=[{'id':f'S{i}'} for i in range(1,20)]
        deliveries=[{'files':[dict(path='evidence.json',content=json.dumps({'sources':sources}))]}]
        output={'files':{'result.json':dict(sources=sources[:12],findings=[dict(source_ids=['S15','S16'])],body='S17 S19')}}
        before=copy.deepcopy((node,deliveries,output))
        report=contracts(node,deliveries,output)[0]
        self.assertEqual(report['max_items'],12)
        self.assertEqual(report['missing_ids'],['S15','S16','S17','S19'])
        self.assertEqual(report['required_count'],16)
        self.assertTrue(report['over_budget'])
        self.assertEqual(set(report['dependency_ids']),{s['id'] for s in sources})
        self.assertEqual((node,deliveries,output),before)
        output['files']['result.json']['sources']=sources
        report=contracts(node,deliveries,output)[0]
        self.assertEqual(report['missing_ids'],[])
        self.assertTrue(report['over_budget'])

    def test_uses_rule_prefix_and_ignores_truncated_dependencies(self):
        node={'outputs':{'r.json':{}},'rules':[dict(kind='text_references',file='r.json',paths=['body'],target_path='refs.*.id',prefix='E')]}
        output={'files':{'r.json':dict(refs=[dict(id='E1')],body='E1 E2 S3 XE4')}}
        report=contracts(node,[{'files':[dict(path='x.json',content='{"refs":[{"id":"E9"}]}',truncated=True)]}],output)[0]
        self.assertEqual(report['missing_ids'],['E2'])
        self.assertEqual(report['dependency_ids'],[])
        self.assertFalse(report['over_budget'])

    def test_selection_rejects_unknown_or_excess_sources_and_preserves_frozen_schema(self):
        contract=dict(file='r.json',target_path='sources.*.id',structured_paths=['source_ids.*'],max_items=2,dependency_ids=['S1','S2','S3'])
        model=output_model(dict(outputs=selection_outputs([contract])))
        for ids in [['S1','S9'],['S1','S2','S3'],['S1','S1'],[]]:
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                model.model_validate(dict(files={'reference-selection-0.json':dict(selected_ids=ids,reason='覆盖需求')}))
        node={'outputs':{'r.json':dict(type='object',properties=dict(sources=dict(type='array',maxItems=2,items=dict(type='object',properties=dict(id=dict(type='string')))),source_ids=dict(type='array',items=dict(type='string')),body=dict(type='string')))}}
        contract['text_rules']=[dict(paths=['body'],prefix='S')]
        before=copy.deepcopy(node);contract['selected_ids']=['S1','S3']
        draft=authoring_node(node,[contract]);self.assertEqual(node,before)
        schema=draft['outputs']['r.json']['properties']
        self.assertEqual(schema['sources']['maxItems'],2)
        self.assertEqual(schema['sources']['items']['properties']['id']['enum'],['S1','S3'])
        self.assertEqual(schema['source_ids']['items']['enum'],['S1','S3'])
        model=output_model(draft)
        for body in ['S2','（S12）','S1\nS3\nS9']:
            with self.subTest(body=body),self.assertRaises(ValueError):model.model_validate(dict(files={'r.json':dict(body=body)}))
        model.model_validate(dict(files={'r.json':dict(body='S1\n（S3），AS2不是引用')}))
