"""Controlled binary artifact imports and media boundaries."""
import io
import unittest
import zipfile

from orchpact.services.tool_artifacts import ToolArtifacts, inspect_media
from verification_fixtures import PDF_BYTES, PDF_MEDIA, VerificationFixture, artifact_entry, renderer_result, workflow

DOCX='application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def archive(entries):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as value:
        for name,data,attributes in entries:
            info=zipfile.ZipInfo(name);info.compress_type=zipfile.ZIP_DEFLATED
            if attributes is not None:info.external_attr=attributes
            value.writestr(info,data)
    return stream.getvalue()


def docx(content_type=DOCX):
    return archive([
        ('[Content_Types].xml',f'<Types><Override ContentType="{content_type}"/></Types>'.encode(),None),
        ('word/document.xml',b'<document/>',None),
    ])


class MediaTests(unittest.TestCase):
    def test_ooxml_and_pdf_structure(self):
        inspect_media(PDF_BYTES,PDF_MEDIA,'deployment.pdf')
        inspect_media(docx(),DOCX,'deployment.docx')
        with self.assertRaises(ValueError):inspect_media(docx('text/plain'),DOCX,'deployment.docx')
        with self.assertRaises(ValueError):inspect_media(b'not-a-pdf',PDF_MEDIA,'deployment.pdf')
        with self.assertRaises(ValueError):inspect_media(b'{}','image/gif','x.gif')

    def test_zip_links_paths_and_compression_bombs_are_rejected(self):
        link=zipfile.ZipInfo('link');link.create_system=3;link.external_attr=(0o120777<<16)
        cases=[archive([('../escape',b'x',None)]),archive([('link',b'x',link.external_attr)])]
        with self.assertRaises(ValueError):inspect_media(cases[0],'application/zip','bundle.zip')
        with self.assertRaises(ValueError):inspect_media(cases[1],'application/zip','bundle.zip')
        bomb=archive([('large.txt',b'x'*2_000_000,None)])
        with self.assertRaises(ValueError):inspect_media(bomb,'application/zip','bundle.zip')

    def test_import_rechecks_hash_size_and_media(self):
        node={'artifact_outputs':{'deployment.pdf':{'media_type':PDF_MEDIA,'max_bytes':32}}}
        definition={'artifact_contract':{'media_types':[PDF_MEDIA],'max_files':1,'max_bytes':100}}
        runtime=ToolArtifacts()
        with self.assertRaises(ValueError):runtime.import_result(node,definition,{'artifacts':{'deployment.pdf':artifact_entry(sha='0'*64)}})
        with self.assertRaises(ValueError):runtime.import_result(node,definition,{'artifacts':{'deployment.pdf':artifact_entry()}})
        with self.assertRaises(ValueError):runtime.import_result(node,definition,{'artifacts':{'deployment.pdf':artifact_entry(b'{}','application/json')}})


class ArtifactWorkflowTests(VerificationFixture):
    def test_binary_root_delivery_uses_immutable_artifact_and_null_json_result(self):
        value=workflow();self.install(value);run=self.invoke()
        self.assertTrue(self.app.state.runner.step())
        view=self.view(run)
        self.assertEqual(view['status'],'completed');self.assertIsNone(view['result'])
        self.assertEqual(view['verification_evidence'],[])
        response=self.client.get('/api/v1/artifacts/'+view['artifact_id']+'/download')
        self.assertEqual(response.status_code,200);self.assertEqual(response.content,PDF_BYTES)
        self.assertEqual(response.headers['content-type'],PDF_MEDIA)

    def test_json_and_binary_outputs_share_existing_artifact_storage(self):
        value=workflow(mixed=True,root_artifact=False);self.install(value);run=self.invoke()
        self.assertTrue(self.app.state.runner.step())
        view=self.view(run);self.assertEqual(view['status'],'completed');self.assertEqual(view['result'],{'value':7})
        render=next(task for task in view['tasks'] if task['node_key']=='render')
        response=self.client.get('/api/v1/artifacts/'+render['artifact_id']+'/download')
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:self.assertEqual(set(bundle.namelist()),{'result.json','deployment.pdf'})

    def test_invalid_hash_or_media_content_never_publishes(self):
        for mode in ('hash','media'):
            value=workflow();self.install(value);run=self.invoke('invalid-'+mode)
            if mode=='hash':self.renderer.result=lambda context:{'artifacts':{'deployment.pdf':artifact_entry(sha='0'*64)}}
            else:self.renderer.result=lambda context:renderer_result(context,data=b'not-a-pdf')
            self.assertTrue(self.app.state.runner.step())
            view=self.view(run);self.assertNotEqual(view['status'],'completed');self.assertIsNone(view['result'])
            with self.s.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0],0)
            self.assertEqual(self.view(run)['verification_evidence'],[])
            self.tearDown();self.setUp()


if __name__=='__main__':unittest.main()
