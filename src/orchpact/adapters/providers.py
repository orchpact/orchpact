"""Local executor and provider adapters. There is deliberately no remote worker API."""
import base64
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from datetime import datetime,timezone
import httpx
from pydantic import ValidationError
from ..adapters.database import ident,now,dumps,loads
from ..kernel.models import WorkOutput,PlanningDecision,IntakeDecision
from ..credits import policy_snapshot, reservation_amount

from ..search import profile_config, search_request, normalize, merge, public_url, quota_source, source_usage, SearchQuotaExceeded
from ..json_protocol import decode
from ..reference_feedback import contracts as reference_contracts, selection_outputs, authoring_node
from ..provider_http import bounded_json, write_receipt

class Providers:
    def __init__(self,service):self.service=service;self.db=service.db;self.settings=service.settings

    def reserve(self,ctx,kind,model,token_bound=None):
        if ctx.get('_crew_member_key'):
            capability='search' if kind=='search' else 'text'
            if capability not in loads(ctx['snapshot']['agent']['capabilities']):
                from ..kernel.errors import Forbidden
                raise Forbidden('本岗位未声明调用所需能力')
        with self.db.tx() as c:
            task=self.service.current(c,ctx['task']['id'],ctx['task']['attempt_id'])
            p=self.service.row(c,'projects',task['project_id'])
            grant=self.service.policies.authorize(c,ctx['execution_context'],'call',p, 'search' if kind=='search' else ('image' if ctx['snapshot']['profile']['kind']=='image' else 'text'))
            limit=p['search_limit'] if kind=='search' else p['request_limit']
            if kind=='search':
                source=quota_source(model)
                if source_usage(c,p['id'])[source]>=limit:
                    raise SearchQuotaExceeded(f'项目搜索来源{source}调用已达硬上限{limit}')
            else:
                count=c.execute('SELECT count(*) FROM requests WHERE project_id=%s AND kind=%s',(p['id'],kind)).fetchone()[0]
                if count>=limit:raise ValueError(f'项目{kind}调用已达硬上限{limit}')
            config=getattr(self.service,'configuration',None)
            policy=config.charge_policy(kind,model,c,defaults=self.settings.credits) if config else policy_snapshot(self.settings.credits)
            reserved=reservation_amount(kind,policy,token_bound)
            blocked=self.service.policies.budget_block(c,task,reserved,grant)
            if not blocked:
                key=ident();c.execute('''INSERT INTO requests(id,project_id,attempt_id,kind,model_id,status,created,credit_policy,
                    reserved_points_micros,mandate_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (key,p['id'],task['attempt_id'],kind,model,'started',now(),dumps(policy),str(reserved),grant['id'] if grant else None))
                if getattr(self.service,'access',None):self.service.access.wallets.reserve(c,key,p,reserved)
                return key
            self.db.event(c,p['id'],None,'policy.blocked',gate='budget',
                          unit_id=task['unit_id'],**blocked)
        raise ValueError(blocked['summary'])

    def credential(self,profile):
        config=getattr(self.service,'configuration',None)
        return config.secret(profile['credential']) if config and profile.get('credential') else os.environ[profile['key_env']]

    def record(self,key,status,usage=None,response_id=None,error=None):
        with self.db.tx() as c:
            # Receipts may be replayed after recovery; a settled debit is immutable.
            if getattr(self.service,'access',None):
                charge=c.execute('SELECT state FROM credit_charges WHERE request_id=%s',(key,)).fetchone()
                if charge and charge['state']=='settled':
                    # A late duplicate must not rewrite the immutable settled request either.
                    return
            c.execute('UPDATE requests SET status=%s,usage=%s,response_id=%s,error=%s WHERE id=%s',
                (status,dumps(usage or {}),response_id,error,key))
            if getattr(self.service,'access',None):self.service.access.wallets.settle(c,key)

    def recover_receipts(self):
        """Settle durable provider responses after a local crash or write failure."""
        recovered=0
        root=self.settings.data/'receipts'
        if not root.exists():return 0
        for path in root.glob('*/*.json'):
            with self.db.connect() as c:
                row=c.execute('SELECT kind,status FROM requests WHERE id=%s',(path.stem,)).fetchone()
            if not row or row['status']=='completed':continue
            try:
                receipt=loads(path.read_text())
                if row['kind']=='search':
                    response=receipt.get('response')
                    if not isinstance(response,dict):continue
                    usage=dict(provider=receipt['provider'],operation=receipt['operation'],provider_usage=response.get('usage',{}))
                    response_id=None
                else:
                    if not isinstance(receipt.get('usage'),dict):continue
                    usage=receipt['usage'];response_id=receipt.get('response_id')
                self.record(path.stem,'completed',usage,response_id)
                recovered+=1
            except (OSError,ValueError,KeyError,TypeError) as exc:
                import logging
                logging.warning('provider receipt recovery deferred: %s',type(exc).__name__)
        return recovered

    def agent_json(self,ctx,prompt,context,schema,allow_tools=True):
        from .crewai import run_crew
        return run_crew(self,ctx,prompt,context,schema,allow_tools)

    def json(self,ctx,prompt,context,schema):
        profile=ctx['snapshot']['profile']
        system=prompt+'\n仅返回一个严格JSON实例。以下是字段约束（不要返回schema本身）：\n'+dumps(schema.model_json_schema())
        if hasattr(schema,'document_schemas'):
            system+='\nfiles中每个文件值必须符合以下独立JSON Schema（各自的$ref以该文件结构为根）。必填字段和字段名称以此为准，不能用近义名称替代；说明文字可放在结构允许的其他字段。关闭全部嵌套括号和最外层files/响应对象：\n'+dumps(schema.document_schemas())
        recovered=self.recover_json(ctx,system,context,schema)
        if recovered is not None:return recovered
        messages=[dict(role='system',content=system),dict(role='user',content=dumps(context))]
        selected=[dict(file=c['file'],selected_ids=c['selected_ids'],max_items=c['max_items']) for c in context.get('reference_contracts',[]) if c.get('selected_ids')]
        if selected:messages.append(dict(role='user',content='本次报告的引用集合已选定：'+dumps(selected)+'。只使用这些原始编号及其真实URL。逐段重新撰写并核对所有正文和结构化引用；未选来源的独有细节不得沿用或改挂其他编号，相关缺口须保留为待核实。最终sources完整列出使用的来源。'))
        attempt=ctx['task']['attempt_id'];directory=self.settings.data/'receipts'/attempt;directory.mkdir(parents=True,exist_ok=True)
        for index,tokens in enumerate((16000,24000),1):
            body=dict(model=profile['model'],messages=messages,response_format={'type':'json_object'},max_tokens=tokens,temperature=0)
            # Conservative token reservation: UTF-8 bytes plus output limit and
            # protocol overhead. Provider-reported usage remains authoritative.
            bound=len(dumps(body).encode('utf-8'))+tokens+1024
            request=self.reserve(ctx,'model',profile['id'],token_bound=bound)
            if profile.get('thinking_disabled'):body['thinking']={'type':'disabled'}
            receipt_path=directory/(request+'.json')
            try:
                if getattr(self.service,'stop_event',None) and self.service.stop_event.is_set():raise RuntimeError('服务正在停止')
                with httpx.Client(timeout=httpx.Timeout(120,connect=15),follow_redirects=False,trust_env=False) as client:
                    response=client.post(profile['base_url'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+self.credential(profile)},json=body)
                    response.raise_for_status();data=bounded_json(response)
                choice=data['choices'][0];raw=choice['message'].get('content') or '';reason=choice.get('finish_reason')
                receipt=dict(request_id=request,model_id=profile['id'],messages=messages,raw=raw,finish_reason=reason,usage=data.get('usage',{}),response_id=data.get('id'))
                write_receipt(receipt_path,receipt)
                self.record(request,'completed',data.get('usage'),data.get('id'))
            except Exception as exc:
                if receipt_path.is_file():
                    # A durable provider response is never reclassified as an ordinary transport failure.
                    try:self.record(request,'unknown',data.get('usage',{}),data.get('id'),error='receipt_pending_settlement')
                    except Exception:pass
                    raise RuntimeError('供应商响应已保存，等待恢复结算；不会自动重新请求') from None
                self.record(request,'failed',error=type(exc).__name__)
                raise RuntimeError('模型请求失败：'+type(exc).__name__+'；详情类型已记录，不会回退到模拟') from None
            decoded=None
            try:
                if reason!='stop':raise ValueError('供应商未正常结束：'+str(reason))
                decoded,suffix=decode(raw)
                value=schema.model_validate(decoded).model_dump()
                if suffix:
                    normalization=suffix if isinstance(suffix,dict) else dict(kind='missing_terminal_delimiters',appended=suffix)
                    (directory/(request+'-normalization.json')).write_text(dumps(dict(
                        **normalization,validated=True)))
                return value
            except (ValueError,ValidationError) as exc:
                detail=(dumps(exc.errors(include_input=False,include_context=False,include_url=False)) if isinstance(exc,ValidationError) else str(exc))[:2000]
                (directory/(request+'-validation.json')).write_text(dumps(dict(error=detail,committed=False)))
                if index==2:raise ValueError('两次生成未通过JSON协议校验；未提交动作：'+detail) from None
                if len(raw)<=32000:messages.append(dict(role='assistant',content=raw))
                if isinstance(decoded,dict) and context.get('stage_contract'):
                    report_contracts=reference_contracts(context['stage_contract'],context.get('deliveries',[]),decoded)
                    if report_contracts:messages.append(dict(role='user',content='同时修正所有引用约束，不得只处理当前第一条错误：'+dumps(report_contracts)))
                messages.append(dict(role='user',content='草稿未提交。请按相同输入重新输出完整且简洁的JSON，保留事实与缺口，不补造数据。错误：'+detail))
        raise RuntimeError('unreachable')

    def recover_json(self,ctx,system,context,schema):
        """Retry an unchanged planned worker using its already paid response.

        Only failures due to redundant root delimiters qualify. Keep
        original receipts/charges immutable; normal business checks and gates
        still run through CrewAIExecutor after this schema validation.
        """
        node=ctx.get('planned_node')
        if not node or node.get('gate') or ctx['task']['kind']!='work':return None
        if context.get('output_feedback'):return None
        with self.db.connect() as c:
            rows=c.execute('''SELECT r.id,r.attempt_id FROM requests r JOIN attempts a ON a.id=r.attempt_id
                WHERE a.task_id=%s AND r.project_id=%s AND r.kind='model' AND r.status='completed'
                AND a.status='failed' AND a.error LIKE '两次生成未通过JSON协议校验%%Extra data%%'
                AND a.id!=%s ORDER BY r.created DESC,r.id DESC''',
                (ctx['task']['id'],ctx['task']['project_id'],ctx['task']['attempt_id'])).fetchall()
        for row in rows:
            path=self.settings.data/'receipts'/row['attempt_id']/(row['id']+'.json')
            if not path.is_file():continue
            try:
                receipt=loads(path.read_text());messages=receipt['messages']
                if messages[0]!=dict(role='system',content=system):continue
                previous=loads(messages[1]['content'])
                keys=('goal','application','stage_contract','inputs','deliveries','evidence','output_feedback','agent_history','crew_member','crew_instruction','agent_tools')
                if any(previous.get(k)!=context.get(k) for k in keys):continue
                # Retrying adds the platform's own rejection notice to the
                # project conversation. It carries no new user instruction.
                def substantive(messages):
                    return [m for m in messages if not (
                        m.get('from_task')==ctx['task']['id'] and m.get('kind')=='rejected'
                        and m.get('content','').startswith('执行失败：两次生成未通过JSON协议校验')
                        and 'Extra data' in m.get('content',''))]
                if substantive(previous.get('messages',[]))!=substantive(context.get('messages',[])):continue
                if any(previous['task'].get(k)!=context['task'].get(k) for k in ('id','description','expected','kind')):continue
                if receipt['model_id']!=ctx['snapshot']['profile']['id']:continue
            except (ValueError,KeyError,TypeError,OSError):continue
            try:
                if receipt['finish_reason']!='stop':return None
                decoded,normalization=decode(receipt['raw'])
                if not isinstance(normalization,dict) or normalization['kind']!='redundant_terminal_delimiters':return None
                value=schema.model_validate(decoded).model_dump()
            except (ValueError,KeyError,TypeError):return None
            directory=self.settings.data/'receipts'/ctx['task']['attempt_id']
            with self.db.tx() as c:
                self.service.current(c,ctx['task']['id'],ctx['task']['attempt_id'])
                directory.mkdir(parents=True,exist_ok=True)
                audit=dict(**normalization,validated=True,source_request_id=row['id'],
                    source_attempt_id=row['attempt_id'],raw_sha256=hashlib.sha256(receipt['raw'].encode()).hexdigest())
                (directory/(row['id']+'-recovery.json')).write_text(dumps(audit))
                self.db.event(c,ctx['task']['project_id'],ctx['task']['id'],'model_response_reused',
                    recovery=audit,summary='原输入和依赖未变，仅移除响应末尾冗余括号；复用已结算响应，不新增模型调用或扣费')
            return value
        return None

    def search_budget(self,ctx):
        with self.db.connect() as c:
            project=self.service.row(c,'projects',ctx['task']['project_id'])
            used=source_usage(c,project['id'])
        return {source:max(0,project['search_limit']-count) for source,count in used.items()}

    def search_remaining(self,ctx,source=None):
        budget=self.search_budget(ctx)
        if source is not None:return budget[source]
        return sum(budget[p['id']] for p in profile_config(ctx['snapshot']['profile'])['providers'])

    def previous_evidence(self,ctx):
        """Reuse only server-recorded evidence for this unchanged task and inputs."""
        with self.db.connect() as c:
            rows=c.execute('''SELECT r.id,r.attempt_id FROM requests r JOIN attempts a ON a.id=r.attempt_id
                WHERE a.task_id=%s AND r.project_id=%s AND r.kind='model' AND r.status='completed'
                AND a.id!=%s ORDER BY r.created DESC''',
                (ctx['task']['id'],ctx['task']['project_id'],ctx['task']['attempt_id'])).fetchall()
            for row in rows:
                directory=self.settings.data/'receipts'/row['attempt_id']
                path=directory/(row['id']+'.json')
                if not path.is_file():continue
                try:
                    receipt=loads(path.read_text())
                    previous=loads(receipt['messages'][1]['content'])
                    evidence=previous.get('evidence')
                    if not evidence or previous['task']['id']!=ctx['task']['id']:continue
                    if previous['task']['description']!=ctx['task']['description'] or previous['inputs']!=ctx['inputs']:continue
                    if previous['goal']!=ctx['project']['goal']:continue
                    if previous.get('application')!=ctx.get('application'):continue
                    if previous.get('stage_contract')!=ctx.get('planned_node'):continue
                    calls=[call for entry in evidence for call in entry['calls'] if call.get('request_id')]
                    actual={r['id']:r['status'] for r in c.execute(
                        "SELECT id,status FROM requests WHERE attempt_id=%s AND kind='search'",(row['attempt_id'],))}
                    if not actual or {call['request_id'] for call in calls}!=set(actual):continue
                    if any(actual[call['request_id']] not in ('completed','failed') or actual[call['request_id']]!=call['status'] or not (directory/(call['request_id']+'.json')).is_file() for call in calls):continue
                    return evidence
                except (ValueError,KeyError,TypeError,OSError):continue
        return None

    def search_call(self,ctx,provider,query,count,operation='search'):
        name=provider['id']
        # Reserve each actual engine/fetch call, not each logical query. No hidden retries.
        try:key=self.reserve(ctx,'search','search:'+name+':'+operation)
        except SearchQuotaExceeded:
            # Another worker may claim the final slot after our advisory check.
            return [],dict(provider=name,operation=operation,status='skipped',reason='项目内该搜索来源次数上限')
        directory=self.settings.data/'receipts'/ctx['task']['attempt_id'];directory.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now(timezone.utc).isoformat()
        receipt_path=directory/(key+'.json')
        try:
            if getattr(self.service,'stop_event',None) and self.service.stop_event.is_set():raise RuntimeError('服务正在停止')
            with httpx.Client(timeout=45,follow_redirects=False,trust_env=False) as client:
                secret=self.credential(provider)
                if operation=='fetch':
                    if name!='deepseek' or not public_url(query):raise ValueError('只允许抓取真实检索返回的公共网页')
                    response=client.post(provider.get('base_url','https://api.deepseek.com/v1/tools/fetch'),headers={'Authorization':'Bearer '+secret},json={'url':query})
                    response.raise_for_status();data=bounded_json(response)
                    if not isinstance(data,dict) or not isinstance(data.get('content'),str) or not data['content'].strip():raise ValueError('网页抓取未返回正文')
                    sources=[dict(url=query,title=str(data.get('title') or '')[:500],excerpt=data['content'][:6000],provider=name,operation='fetch',published_date=None)]
                else:
                    data=search_request(client,provider,secret,query,count)
                    sources=normalize(name,data,count)
            for source in sources:source.update(request_id=key,retrieved_at=stamp)
            write_receipt(receipt_path,dict(query=query,provider=name,operation=operation,retrieved_at=now(),retrieved_at_iso=stamp,response=data))
            usage=dict(provider=name,operation=operation,provider_usage=data.get('usage',{}))
            self.record(key,'completed',usage)
            return sources,dict(provider=name,operation=operation,status='completed',request_id=key,results=len(sources),retrieved_at=stamp)
        except Exception as exc:
            status=exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
            detail=type(exc).__name__+((' HTTP '+str(status)) if status else '')
            if receipt_path.is_file() and 'response' in loads(receipt_path.read_text()):
                try:self.record(key,'unknown',dict(provider=name,operation=operation,provider_usage=data.get('usage',{})),error='receipt_pending_settlement')
                except Exception:pass
                return [],dict(provider=name,operation=operation,status='unknown',request_id=key,error='供应商响应已保存，等待恢复结算',retrieved_at=stamp)
            write_receipt(receipt_path,dict(query=query,provider=name,operation=operation,retrieved_at=now(),retrieved_at_iso=stamp,error=detail))
            self.record(key,'failed',dict(provider=name,operation=operation),error=detail)
            return [],dict(provider=name,operation=operation,status='failed',request_id=key,error=detail,retrieved_at=stamp)

    def search(self,ctx,query):
        if not isinstance(query,str) or not query.strip() or len(query)>128:raise ValueError('搜索词须为1到128字符')
        config=profile_config(ctx['snapshot']['profile']);groups=[];calls=[]
        for provider in config['providers']:
            if not self.search_remaining(ctx,provider['id']):
                calls.append(dict(provider=provider['id'],operation='search',status='skipped',reason='项目内该搜索来源次数上限'));continue
            results,call=self.search_call(ctx,provider,query,config['max_results'])
            calls.append(call);groups.append(results)
            if results and config['mode']=='fallback':break
        sources=merge(groups)
        if not sources:
            raise RuntimeError('搜索未获得可用证据：'+dumps(calls))
        deepseek=config.get('fetch_provider') if 'fetch_provider' in config else next((p for p in config['providers'] if p['id']=='deepseek'),None)
        if deepseek:
            for source in sources[:config['fetch_top']]:
                if not self.search_remaining(ctx,'deepseek'):
                    calls.append(dict(provider='deepseek',operation='fetch',status='skipped',reason='项目内该搜索来源次数上限'));break
                results,call=self.search_call(ctx,dict(deepseek,id='deepseek'),source['url'],1,'fetch');calls.append(call)
                if results:
                    source['fetched_content']=results[0]['excerpt']
                    source['fetch_request_id']=call['request_id']
        return dict(query=query,retrieved_at=datetime.now(timezone.utc).isoformat(),mode=config['mode'],sources=sources,calls=calls,
                    limited=any(c['status']!='completed' or c.get('results')==0 for c in calls),
                    verification_note='搜索引擎是检索渠道，不是独立证据。同一URL只算一个网页；交叉核验须检查不同原始发布者、日期及正文。authority仅为供应商评分，不能当成事实已验证。失败、跳过或无结果的渠道须在局限中说明。')

    def image(self,ctx):
        from ..image_providers import generate,image_format
        profile=ctx['snapshot']['profile']
        request=ctx['snapshot'].get('image_request') or dict(prompt=ctx['snapshot']['prompt']+'\n'+ctx['task']['description'],aspect_ratio='1:1',reference_artifact_ids=[])
        references=[];metadata=[]
        with self.db.connect() as c:
            for aid in request.get('reference_artifact_ids',[]):
                artifact=self.service.row(c,'artifacts',aid);content=self.service.artifacts.path(artifact).read_bytes()
                ext,mime=image_format(content);references.append((content,mime));metadata.append(dict(artifact_id=aid,sha256=hashlib.sha256(content).hexdigest()))
        key=self.reserve(ctx,'model',profile['id'])
        directory=self.settings.data/'receipts'/ctx['task']['attempt_id'];directory.mkdir(parents=True,exist_ok=True)
        receipt_path=directory/(key+'.json')
        try:
            if getattr(self.service,'stop_event',None) and self.service.stop_event.is_set():raise RuntimeError('服务正在停止')
            with httpx.Client(timeout=httpx.Timeout(240,connect=15),follow_redirects=False,trust_env=False) as client:
                content,usage,effective=generate(client,profile,self.credential(profile),request,references)
            ext,mime=image_format(content)
            write_receipt(receipt_path,dict(request_id=key,model_id=profile['id'],prompt=request['prompt'],negative_prompt=request.get('negative_prompt',''),references=metadata,effective_options=effective,
                usage=usage,sha256=hashlib.sha256(content).hexdigest(),bytes=len(content),format=ext))
            self.record(key,'completed',usage)
            return [dict(path='image.'+ext,data=content)]
        except Exception as exc:
            status=exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
            detail=type(exc).__name__+((' HTTP '+str(status)) if status else '')
            if receipt_path.is_file():
                try:self.record(key,'unknown',usage,error='receipt_pending_settlement')
                except Exception:pass
                raise RuntimeError('图像供应商响应已保存，等待恢复结算；不会自动重新请求') from None
            self.record(key,'failed',error=detail)
            raise RuntimeError('图像生成失败：'+detail+'；不会自动切换供应商') from None
