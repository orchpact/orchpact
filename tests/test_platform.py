"""Bounded immutable file delivery; no generated program is executed."""
import io,zipfile
from runtime_fixtures import KernelFixture,package
from orchpact.adapters.artifacts import encode_files,text_name

class PlatformTests(KernelFixture):
    def test_unsafe_paths_duplicate_names_and_size_limits(self):
        for name in ('../x','/tmp/x','a/../x','a\\x','C:x','a//x','a/./x'):
            with self.subTest(name=name),self.assertRaises(ValueError):encode_files([dict(path=name,content='x')])
        with self.assertRaises(ValueError):encode_files([dict(path='a',content='x'),dict(path='A',content='y')])
        with self.assertRaises(ValueError):encode_files([dict(path='x',data=b'x'*(10*1024*1024+1))])
    def test_multifile_zip_round_trip_does_not_run_source(self):
        value=package();node=value['operations']['generate']['execution_plan']['nodes'][0]
        node['outputs']['source.json']={'type':'object'}
        run=self.invoke(value);ctx=self.s.claim()
        self.s.complete_work(ctx['execution_context'],ctx['task']['id'],ctx['task']['attempt_id'],'source data only',[dict(path='result.json',content='{"value":2}'),dict(path='source.json',content='{"code":"raise RuntimeError()"}')])
        result=self.view(run);artifact=next(t['artifact_id'] for t in result['tasks'] if t['id']==ctx['task']['id']);response=self.client.get('/api/v1/artifacts/'+artifact+'/download')
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:self.assertEqual(set(z.namelist()),{'result.json','source.json'})
    def test_tampered_artifact_is_rejected(self):
        run=self.invoke();self.finish()
        with self.s.db.connect() as c:artifact=self.s.row(c,'artifacts',self.view(run)['artifact_id'])
        self.s.artifacts.path(artifact).write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.s.artifacts.read_files(artifact)
    def test_read_only_state_has_no_credentials_and_does_not_advance_work(self):
        self.invoke();a=self.client.get('/api/v1/state').json();b=self.client.get('/api/v1/state').json()
        self.assertEqual(a,b);self.assertNotIn('RUNTIME_FIXTURE_KEY',str(a))
    def test_prompt_edits_preserve_task_snapshots(self):
        run=self.invoke();ctx=self.s.claim();unit=ctx['snapshot']['unit']
        self.s.unit(self.ctx,dict(name=unit['name'],purpose=unit['purpose'],prompt='changed',model_id=unit['model_id']),unit['id'])
        with self.s.db.connect() as c:current=self.s.context(c,self.s.row(c,'tasks',ctx['task']['id']))
        self.assertEqual(current['snapshot']['prompt'],ctx['snapshot']['prompt'])
    def test_text_filename_detection(self):
        self.assertEqual(text_name('hello'),'result.txt');self.assertEqual(text_name('# hello'),'result.md')
