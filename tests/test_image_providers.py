from orchpact.testing import database_url_for
import base64,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import httpx
from orchpact.image_providers import generate,image_format,image_options
from orchpact.config import Settings
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII=')
RAW=base64.b64encode(PNG).decode()
REQ=dict(prompt='画角色三视图',negative_prompt='文字、裁切',aspect_ratio='3:1',reference_artifact_ids=[])
def response(data):return httpx.Response(200,request=httpx.Request('POST','https://example.invalid'),json=data)
class ImageProviders(unittest.TestCase):
 def setUp(self):self.client=Mock();self.client.post.return_value=response({'data':[{'b64_json':RAW}],'usage':{'total_tokens':12}})
 def profile(self,provider,**kwargs):return dict(provider=provider,base_url='https://example.invalid/v1',model='test-image',**kwargs)
 def test_openai_generation_omits_unsupported_response_format(self):
  data,usage,meta=generate(self.client,self.profile('openai'),'secret',REQ,[]);kw=self.client.post.call_args.kwargs
  self.assertEqual(data,PNG);self.assertEqual(usage['total_tokens'],12);self.assertNotIn('response_format',kw['json']);self.assertEqual(kw['json']['size'],'1536x512');self.assertIn('排除项',kw['json']['prompt']);self.assertTrue(self.client.post.call_args.args[0].endswith('/images/generations'))
 def test_openai_references_use_multipart_edit_not_json(self):
  generate(self.client,self.profile('openai'),'secret',REQ,[(PNG,'image/png')]);kw=self.client.post.call_args.kwargs
  self.assertTrue(self.client.post.call_args.args[0].endswith('/images/edits'));self.assertEqual(kw['files'][0][0],'image[]');self.assertEqual(kw['files'][0][1][1],PNG);self.assertNotIn('json',kw);self.assertNotIn('response_format',kw['data'])
 def test_seedream_uses_base64_references_explicit_size_and_single_image(self):
  generate(self.client,self.profile('seedream',image_options={'watermark':False}),'secret',REQ,[(PNG,'image/png')]);body=self.client.post.call_args.kwargs['json'];self.assertNotIn('n',body);self.assertEqual(body['response_format'],'b64_json');self.assertTrue(body['image'][0].startswith('data:image/png;base64,'));self.assertEqual(body['size'],'3072x1024');self.assertFalse(body['watermark'])
 def test_gemini_native_transport_and_thought_exclusion(self):
  self.client.post.return_value=response({'candidates':[{'content':{'parts':[{'thought':True,'inlineData':{'data':RAW}},{'text':'done'},{'inlineData':{'mimeType':'image/png','data':RAW}}]}}],'usageMetadata':{'totalTokenCount':42}})
  data,usage,meta=generate(self.client,self.profile('gemini'),'secret',REQ,[(PNG,'image/png')]);kw=self.client.post.call_args.kwargs
  self.assertEqual(data,PNG);self.assertEqual(usage['total_tokens'],42);self.assertIn('generateContent',self.client.post.call_args.args[0]);self.assertEqual(kw['headers'],{'x-goog-api-key':'secret'});self.assertEqual(kw['json']['generationConfig']['imageConfig']['aspectRatio'],'21:9');self.assertIn('inlineData',kw['json']['contents'][0]['parts'][1]);self.assertEqual(meta['requested_aspect_ratio'],'3:1')
 def test_gemini_blocked_and_multiple_images_are_failure(self):
  for data in [{'promptFeedback':{'blockReason':'SAFETY'}},{'candidates':[{'content':{'parts':[{'inlineData':{'data':RAW}},{'inlineData':{'data':RAW}}]}}]}]:
   self.client.post.return_value=response(data)
   with self.assertRaises(ValueError):generate(self.client,self.profile('gemini'),'secret',REQ,[])
 def test_unknown_options_or_provider_fail_instead_of_ignored(self):
  for p in [self.profile('unknown'),self.profile('openai',image_options={'response_format':'url'}),self.profile('gemini',image_options={'quality':'high'}),self.profile('gemini',image_options={'resolution':'3K'})]:
   with self.assertRaises(ValueError):image_options(p)
 def test_generic_adapter_does_not_guess_reference_support(self):
  p=self.profile('openai_compatible');generate(self.client,p,'secret',REQ,[]);self.assertEqual(self.client.post.call_args.kwargs['json']['response_format'],'b64_json')
  with self.assertRaises(ValueError):generate(self.client,p,'secret',REQ,[(PNG,'image/png')])
 def test_arbitrary_url_corrupt_or_multifile_outputs_rejected(self):
  for items in [[{'url':'https://internal.invalid'}],[{'b64_json':'bad'}],[{'b64_json':base64.b64encode(b'not-image').decode()}],[{'b64_json':RAW},{'b64_json':RAW}]]:
   self.client.post.return_value=response({'data':items})
   with self.assertRaises((ValueError,TypeError)):generate(self.client,self.profile('seedream'),'secret',REQ,[])
 def test_config_auto_enable_and_no_key_leak(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);database_url_for(root/'var');(root/'config').mkdir();(root/'config/models.toml').write_text('''[models.text]
kind="text"
model="test"
base_url="https://example.invalid/v1"
key_env="TEXT_KEY"
json_mode=true
[models.picture]
kind="image"
provider="gemini"
model="gemini-3-pro-image"
base_url="https://example.invalid/v1beta"
key_env="GEMINI_TEST_KEY"
enabled="auto"
''')
   with patch.dict(os.environ,{'ORCHPACT_API_TOKEN':'test-secret-'*4,'TEXT_KEY':'text-secret'},clear=True):
    settings=Settings.load(root);self.assertEqual(len(settings.profiles),1);self.assertEqual(settings.image_catalog()[0]['status'],'missing_credentials')
   with patch.dict(os.environ,{'ORCHPACT_API_TOKEN':'test-secret-'*4,'TEXT_KEY':'text-secret','GEMINI_TEST_KEY':'image-secret'},clear=True):
    settings=Settings.load(root);self.assertEqual(settings.choose(['image_reference'])['id'],'picture');self.assertNotIn('image-secret',json.dumps(settings.catalog()));self.assertNotIn('image-secret',json.dumps(settings.image_catalog()))
if __name__=='__main__':unittest.main()
