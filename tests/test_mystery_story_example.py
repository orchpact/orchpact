"""Narrative state checks and the explicit limit of self-reported annotations."""
from copy import deepcopy
import json
from pathlib import Path
import runpy
import unittest

HERE=Path(__file__).resolve().parents[1]/'examples/mystery-story'
VERIFIER=runpy.run_path(str(HERE/'verify.py'))
CHECK=VERIFIER['check_story']
CHECK_EDITOR=VERIFIER['check_editor_facts']


class MysteryStoryTests(unittest.TestCase):
    def setUp(self):
        self.canon=json.loads((HERE/'input.json').read_text())
        self.story=json.loads((HERE/'reference-draft.json').read_text())

    def test_reference_has_two_paths_but_no_automatic_semantic_guarantee(self):
        result=CHECK(self.canon,self.story)
        self.assertEqual(result['status'],'PASS');self.assertEqual(len(result['paths']),2)
        self.assertEqual(result['characters'],1154)
        self.assertEqual(result['semantic_status'],'NOT_VERIFIED');self.assertTrue(result['human_review_required'])

    def test_changed_prose_can_satisfy_the_same_state_contract(self):
        self.story['scenes'][1]['text']='窗外的风更急了。'+self.story['scenes'][1]['text']
        self.assertEqual(CHECK(self.canon,self.story)['status'],'PASS')

    def test_knowledge_timeline_branch_and_payoff_violations_are_rejected(self):
        cases=[]
        def case(code,mutate):
            story=deepcopy(self.story);mutate(story);cases.append((code,story))
        case('knowledge_leak',lambda story:story['scenes'][1].update(uses_secrets=['K1']))
        case('unknown_clue',lambda story:story['scenes'][1].update(uses_clues=['C3']))
        case('early_secret',lambda story:story['scenes'][0].update(learns_secrets=['K1']))
        case('illegal_discovery',lambda story:story['scenes'][0].update(learns_clues=['C1','C3']))
        case('unresolved_clue',lambda story:story['scenes'][-1].update(payoffs=['C1','C2']))
        case('unreachable_ending',lambda story:story['transitions'].pop())
        case('timeline',lambda story:story['scenes'][1].update(minute=0))
        case('identical_endings',lambda story:story['scenes'][-1].update(text=story['scenes'][-2]['text']))
        case('scene_set',lambda story:story['scenes'].append(deepcopy(story['scenes'][0])))
        for code,story in cases:
            with self.subTest(code=code):
                result=CHECK(self.canon,story)
                self.assertEqual(result['status'],'FAIL')
                self.assertIn(code,{row['code'] for row in result['violations']})
                if code=='unknown_clue':
                    issue=next(row for row in result['violations'] if row['code']==code)
                    self.assertEqual(issue['path'],'S2.uses_clues')
                    self.assertIn('lin',issue['message']);self.assertIn('C3',issue['message'])
                if code=='early_secret':
                    issue=next(row for row in result['violations'] if row['code']==code)
                    self.assertIn('K1',issue['message']);self.assertIn('40',issue['message'])

    def test_unannotated_prose_leak_is_a_known_blindspot_requiring_human_review(self):
        self.story['scenes'][0]['text']='林澈当场断定周馆长拼接录音掩盖调换展品的时间。'+self.story['scenes'][0]['text']
        result=CHECK(self.canon,self.story)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['semantic_status'],'NOT_VERIFIED')
        self.assertTrue(result['human_review_required'])

    def test_payoff_feedback_identifies_secret_id_and_allowed_clues(self):
        self.story['scenes'][-1]['payoffs'].append('K1')
        result=CHECK(self.canon,self.story)
        issue=next(row for row in result['violations'] if row['code']=='invented_payoff')
        self.assertEqual(issue['path'],'E2.payoffs')
        self.assertIn('K1',issue['message'])
        self.assertIn('never secret IDs',issue['message'])
        for clue in ('C1','C2','C3'):self.assertIn(clue,issue['message'])

    def test_editor_must_bind_every_frozen_fact_to_both_actual_endings(self):
        report=dict(fact_checks=[dict(secret_id='K1',ending_id=scene['id'],canonical_fact=self.canon['secrets'][0]['description'],quote=scene['text'],verdict='MATCH',reason='Reference assessment') for scene in self.story['scenes'] if scene['id'] in self.canon['endings']])
        self.assertEqual(CHECK_EDITOR(self.canon,self.story,report)['status'],'PASS')
        for change,code in (
            (lambda value:value['fact_checks'].pop(),'editor_fact_coverage'),
            (lambda value:value['fact_checks'].append(deepcopy(value['fact_checks'][0])),'editor_fact_coverage'),
            (lambda value:value['fact_checks'][0].update(canonical_fact='只是动过展品'),'editor_fact_binding'),
            (lambda value:value['fact_checks'][0].update(quote='编造的正文引用'),'editor_fact_binding'),
            (lambda value:value['fact_checks'][0].update(verdict='MISMATCH',reason='正文弱化了调换展品的事实'),'editor_fact_mismatch'),
        ):
            with self.subTest(code=code):
                value=deepcopy(report);change(value);result=CHECK_EDITOR(self.canon,self.story,value)
                self.assertEqual(result['status'],'FAIL');self.assertEqual(result['violations'][0]['code'],code)
        report['fact_checks'][0]['verdict']='INCONCLUSIVE'
        self.assertEqual(CHECK_EDITOR(self.canon,self.story,report)['status'],'INCONCLUSIVE')

    def test_ambiguous_author_facts_are_inconclusive(self):
        self.canon['characters'][1]['id']=self.canon['characters'][0]['id']
        self.assertEqual(CHECK(self.canon,self.story)['status'],'INCONCLUSIVE')

    def test_reference_manuscript_is_not_the_verification_oracle(self):
        from unittest.mock import patch
        original_read=Path.read_text
        def read(path,*args,**kwargs):
            self.assertNotEqual(path.name,'reference-draft.json')
            return original_read(path,*args,**kwargs)
        with patch.object(Path,'read_text',read):self.assertEqual(CHECK(self.canon,self.story)['status'],'PASS')
