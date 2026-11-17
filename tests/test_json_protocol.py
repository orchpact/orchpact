import json
import unittest
from orchpact.json_protocol import decode
from orchpact.kernel.contracts import output_model


class JsonProtocolTests(unittest.TestCase):
    def test_document_feedback_locates_missing_fields_without_exposing_values(self):
        model=output_model({'outputs':{'audit.json':{'type':'object','required':['reviewed_artifact_ids'],
            'additionalProperties':False,'properties':{'reviewed_artifact_ids':{'type':'array'}}}}})
        with self.assertRaises(ValueError) as missing:
            model.model_validate({'files':{'audit.json':{}}})
        self.assertIn('files["audit.json"]',str(missing.exception))
        self.assertIn('reviewed_artifact_ids',str(missing.exception))
        with self.assertRaises(ValueError) as envelope:
            model.model_validate({'reviewed_artifact_ids':[], 'private':'SECRET_FIXTURE_CONTENT'})
        self.assertIn('files',str(envelope.exception))
        self.assertNotIn('SECRET_FIXTURE_CONTENT',str(envelope.exception))
        with self.assertRaises(ValueError) as extra:
            model.model_validate({'files':{'audit.json':{'reviewed_artifact_ids':[], 'private':'SECRET_FIXTURE_CONTENT'}}})
        self.assertIn('files["audit.json"]',str(extra.exception))
        self.assertIn('允许字段',str(extra.exception))
        self.assertNotIn('SECRET_FIXTURE_CONTENT',str(extra.exception))

    def test_removes_only_redundant_copies_of_the_root_delimiter(self):
        for value in [{'files':{'result.json':{'text':'内容中的}]与转义引号"原样保留'}}}, [1,{'x':True}]]:
            raw=json.dumps(value,ensure_ascii=False)
            for extra in [raw[-1],raw[-1]*32,raw[-1]+' \n '+raw[-1]]:
                decoded,normalization=decode(raw+extra)
                self.assertEqual(decoded,value)
                self.assertEqual(normalization,dict(kind='redundant_terminal_delimiters',removed=extra))

    def test_rejects_other_trailing_content_and_invalid_complete_prefixes(self):
        for raw in ['{"x":1} explanation','{"x":1}{"y":2}','{"x":1}]','[1]}',
                    '1}', 'true}', '"text"}', '{"x":1}}\u00a0', '{"x":1}'+'}'*33,
                    '{"x":1,"x":2}}','{"x":NaN}}','{"x":"unfinished}}']:
            with self.subTest(raw=raw),self.assertRaises(ValueError):decode(raw)

    def test_complete_values_and_terminal_delimiters_preserve_content(self):
        for value in [{"files": {"plan.json": {"questions": [{"text": '引号"与括号}]'}]}}}, [1, True, None]]:
            raw=json.dumps(value,ensure_ascii=False)
            self.assertEqual(decode(raw),(value,''))
            for n in range(1,3):
                if raw[-n:] and all(c in '}]' for c in raw[-n:]):
                    self.assertEqual(decode(raw[:-n]),(value,raw[-n:]))
                    self.assertEqual(decode(raw[:-n]+' \n'),(value,raw[-n:]))

    def test_refuses_incomplete_values_interior_errors_duplicates_and_nonfinite(self):
        for raw in ['{"x":"unfinished','{"x":','{"x":1,','{"x": [',
                    '{"x":1 "y":2}', '{"x":1]', '{"x":1}garbage',
                    '{"x":1,"x":2}', '{"x":1,"x":2', '{"x":NaN}', '{"x":Infinity}',
                    '{"x":1e', '{"x":tru', '{']:
            with self.subTest(raw=raw),self.assertRaises(ValueError):decode(raw)
