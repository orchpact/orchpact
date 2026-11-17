"""Provider-specific image transport. No application/business rules or URL downloads."""
import base64
import re
from urllib.parse import quote
from .provider_http import bounded_json

MAX_IMAGE=10*1024*1024
PROVIDERS={'openai','seedream','gemini','openai_compatible'}
RATIOS={'1:1','16:9','9:16','3:1','3:2','2:3','4:3','3:4'}

def image_format(content):
    if not content or len(content)>MAX_IMAGE:raise ValueError('图片须为1字节至10MB')
    if content.startswith(b'\x89PNG\r\n\x1a\n'):return 'png','image/png'
    if content.startswith(b'\xff\xd8\xff'):return 'jpg','image/jpeg'
    if content[:4]==b'RIFF' and content[8:12]==b'WEBP':return 'webp','image/webp'
    raise ValueError('仅支持PNG/JPEG/WebP图片')

def decode(raw):
    if not isinstance(raw,str) or not raw or len(raw)>14*1024*1024:raise ValueError('供应商未返回有效Base64图片')
    data=base64.b64decode(raw,validate=True);image_format(data);return data

def image_options(profile):
    provider=profile.get('provider','openai_compatible')
    if provider not in PROVIDERS:raise ValueError('未知图片provider')
    options=profile.get('image_options',{})
    if not isinstance(options,dict):raise ValueError('image_options须为表')
    allowed={'openai':{'quality','output_format','size','ratio_sizes'},'seedream':{'size','watermark','ratio_sizes'},'gemini':{'resolution'},'openai_compatible':{'size'}}[provider]
    if set(options)-allowed:raise ValueError('image_options包含不支持的字段')
    if options.get('quality','auto') not in ('auto','low','medium','high','xhigh','max'):raise ValueError('图片quality无效')
    if options.get('output_format','png') not in ('png','jpeg','webp'):raise ValueError('output_format无效')
    if options.get('resolution','2K') not in ('512','1K','2K','4K'):raise ValueError('resolution无效')
    if 'watermark' in options and not isinstance(options['watermark'],bool):raise ValueError('watermark须为布尔值')
    sizes=options.get('ratio_sizes',{})
    if not isinstance(sizes,dict) or any(k not in RATIOS or not isinstance(v,str) or not re.fullmatch(r'[1-9][0-9]{1,4}x[1-9][0-9]{1,4}',v) for k,v in sizes.items()):raise ValueError('ratio_sizes无效')
    if 'size' in options and (not isinstance(options['size'],str) or not re.fullmatch(r'(auto|[1-4]K|[1-9][0-9]{1,4}x[1-9][0-9]{1,4})',options['size'])):raise ValueError('图片size无效')
    return provider,options

def generate(client,profile,key,request,references):
    provider,opts=image_options(profile);base=profile['base_url'].rstrip('/')
    prompt=request['prompt']
    if request.get('negative_prompt'):prompt+='\n排除项：'+request['negative_prompt']
    ratio=request.get('aspect_ratio','1:1');prompt+='\n目标宽高比：'+ratio
    if references:prompt+='\n参考图按输入顺序提供。保持参考主体的身份特征，按本次提示词调整场景和造型。'
    if provider=='gemini':
        # Gemini has no 3:1 option; use 21:9 and retain exact layout intent in prompt.
        supported_ratio='21:9' if ratio=='3:1' else ratio
        parts=[{'text':prompt}]+[{'inlineData':{'mimeType':mime,'data':base64.b64encode(data).decode()}} for data,mime in references]
        body={'contents':[{'role':'user','parts':parts}],'generationConfig':{'responseModalities':['TEXT','IMAGE'],'imageConfig':{'aspectRatio':supported_ratio,'imageSize':opts.get('resolution','2K')}}}
        response=client.post(base+'/models/'+quote(profile['model'],safe='')+':generateContent',headers={'x-goog-api-key':key},json=body)
        response.raise_for_status();data=bounded_json(response);images=[]
        for candidate in data.get('candidates',[])[:1]:
            for part in candidate.get('content',{}).get('parts',[]):
                inline=part.get('inlineData') or part.get('inline_data')
                if inline and not part.get('thought'):images.append(inline.get('data'))
        if len(images)!=1:raise ValueError('Gemini未返回唯一最终图片，可能被拦截或仅返回文本')
        usage=data.get('usageMetadata',{});normalized=dict(usage)
        if 'totalTokenCount' in usage:normalized['total_tokens']=usage['totalTokenCount']
        return decode(images[0]),normalized,{'provider':provider,'aspect_ratio':supported_ratio,'requested_aspect_ratio':ratio,'resolution':opts.get('resolution','2K')}
    headers={'Authorization':'Bearer '+key};body={'model':profile['model'],'prompt':prompt,'n':1}
    if provider=='openai':
        defaults={'1:1':'1024x1024','16:9':'1536x864','9:16':'864x1536','3:1':'1536x512','3:2':'1536x1024','2:3':'1024x1536','4:3':'1536x1152','3:4':'1152x1536'}
        if profile['model'].startswith(('gpt-image-1.','gpt-image-1-')) or profile['model']=='gpt-image-1':
            defaults.update({'16:9':'1536x1024','9:16':'1024x1536','3:1':'1536x1024'})
        body.update(size=opts.get('ratio_sizes',{}).get(ratio,opts.get('size',defaults[ratio])),quality=opts.get('quality','medium'),output_format=opts.get('output_format','png'))
        if references:
            files=[('image[]',('reference-'+str(i)+'.'+image_format(content)[0],content,mime)) for i,(content,mime) in enumerate(references)]
            response=client.post(base+'/images/edits',headers=headers,data=body,files=files)
        else:response=client.post(base+'/images/generations',headers=headers,json=body)
    else:
        body['response_format']='b64_json'
        if provider=='seedream':
            # Seedream doesn't use OpenAI's n parameter. Disable group generation.
            body.pop('n');body.update(size=opts.get('ratio_sizes',{}).get(ratio,opts.get('size',{'1:1':'2048x2048','16:9':'2560x1440','9:16':'1440x2560','3:1':'3072x1024','3:2':'2496x1664','2:3':'1664x2496','4:3':'2304x1728','3:4':'1728x2304'}[ratio])),watermark=opts.get('watermark',True))
            if references:body['image']=['data:'+mime+';base64,'+base64.b64encode(content).decode() for content,mime in references]
        else:
            if references:raise ValueError('通用兼容适配器未声明参考图协议，请选择明确provider')
            if 'size' in opts:body['size']=opts['size']
        response=client.post(base+'/images/generations',headers=headers,json=body)
    response.raise_for_status();data=bounded_json(response);items=data.get('data',[])
    if len(items)!=1:raise ValueError('供应商必须返回唯一图片；不接收下载URL或多图隐式截取')
    return decode(items[0].get('b64_json')),data.get('usage',{}),{'provider':provider,**{k:v for k,v in body.items() if k in ('size','quality','output_format','watermark')}}
