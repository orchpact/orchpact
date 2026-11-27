"""Check declared narrative state. Prose semantics always need editorial/human review."""
from copy import deepcopy
from collections import defaultdict
import json

from ..adapters.database import loads
from ..kernel.contracts import validate

from .assets import workflow
from .receipts import stage_receipts


def check_editor_facts(canon,story,report):
    """Bind model judgments to frozen facts and real ending text; not a semantic proof."""
    expected={(secret['id'],ending) for secret in canon['secrets'] for ending in canon['endings']}
    facts={secret['id']:secret['description'] for secret in canon['secrets']}
    scenes={scene['id']:scene['text'] for scene in story['scenes']}
    checks=report['fact_checks'];pairs=[(row['secret_id'],row['ending_id']) for row in checks]
    if len(pairs)!=len(set(pairs)) or set(pairs)!=expected:
        return dict(status='FAIL',violations=[dict(code='editor_fact_coverage',path='fact_checks',message='Review every frozen secret in each ending exactly once')])
    issues=[];uncertain=False
    for index,row in enumerate(checks):
        path='fact_checks['+str(index)+']'
        if row['canonical_fact']!=facts[row['secret_id']] or not row['quote'].strip() or row['quote'] not in scenes[row['ending_id']]:
            issues.append(dict(code='editor_fact_binding',path=path,message='Copy the frozen canonical fact exactly and quote actual contiguous ending text; do not invent or weaken either'))
        elif row['verdict']=='MISMATCH':
            issues.append(dict(code='editor_fact_mismatch',path=path,message=row['ending_id']+': preserve '+row['canonical_fact']+'; editorial reason: '+row['reason']))
        elif row['verdict']=='INCONCLUSIVE':uncertain=True
    return dict(status='FAIL' if issues else 'INCONCLUSIVE' if uncertain else 'PASS',violations=issues)


def check_story(canon,story):
    operation=workflow('mystery-story')['operations']['write']
    try:validate(canon,operation['input_schema'])
    except ValueError:return dict(status='INCONCLUSIVE',violations=[],reason='invalid_canon')
    for name in ('characters','clues','secrets','scene_slots'):
        ids=[row['id'] for row in canon[name]]
        if len(ids)!=len(set(ids)):return dict(status='INCONCLUSIVE',violations=[],reason='ambiguous_canon')
    if canon['min_chars']>canon['max_chars']:return dict(status='INCONCLUSIVE',violations=[],reason='conflicting_length')
    schema=next(node for node in operation['execution_plan']['nodes'] if node['key']=='draft')['outputs']['draft.json']
    try:validate(story,schema)
    except ValueError as error:return dict(status='FAIL',violations=[dict(code='schema',message=str(error),path='story')])
    issues=[]
    def reject(code,path,message):
        if len(issues)<32:issues.append(dict(code=code,path=path,message=message))
    slots={row['id']:row for row in canon['scene_slots']}
    scenes={row['id']:row for row in story['scenes']}
    characters={row['id']:row for row in canon['characters']}
    clues={row['id']:row for row in canon['clues']}
    secrets={row['id']:row for row in canon['secrets']}
    if len(scenes)!=len(story['scenes']) or set(scenes)!=set(slots):reject('scene_set','scenes','Scene IDs must be unique and match all fixed slots')
    if story['title']!=canon['title']:reject('title','title','Preserve the commissioned title')
    size=sum(len(row['text']) for row in story['scenes'])
    if not canon['min_chars']<=size<=canon['max_chars']:reject('length','scenes.text',f"Total Unicode characters {size}; required {canon['min_chars']}..{canon['max_chars']}")
    for key,scene in scenes.items():
        if key not in slots:continue
        if any(scene[field]!=slots[key][field] for field in ('minute','location','pov')):reject('scene_slot',key,'Preserve each scene time, location and point of view')
        if scene['pov'] not in characters or scene['location'] not in canon['locations']:reject('character_location',key,'Unknown character or location')
        for field in ('learns_clues','uses_clues','learns_secrets','uses_secrets','payoffs'):
            if len(scene[field])!=len(set(scene[field])):reject('duplicate_annotation',key+'.'+field,'Annotation IDs must be unique')
    edges=defaultdict(list);pairs=[]
    for edge in story['transitions']:
        source,target=edge['source'],edge['target'];pairs.append((source,target));edges[source].append(target)
        if source not in scenes or target not in scenes:reject('edge_reference','transitions','Transition points to an unknown scene')
        elif scenes[target]['minute']<=scenes[source]['minute']:reject('timeline','transitions','Every transition must advance time')
    if len(pairs)!=len(set(pairs)) or set(pairs)!={(edge['source'],edge['target']) for edge in canon['transitions']}:reject('branch_structure','transitions','Preserve all commissioned branch choices')
    if canon['entry'] not in scenes or not set(canon['endings'])<=set(scenes):reject('ending_set','scenes','Entry and both endings must exist')
    paths=[];pending=[(canon['entry'],[])]
    while pending and len(paths)<=64:
        key,path=pending.pop()
        if key not in scenes or key in path:continue
        path=path+[key]
        if not edges[key]:paths.append(path)
        else:pending.extend((target,path) for target in edges[key])
    if pending:return dict(status='INCONCLUSIVE',violations=issues,reason='path_budget')
    reachable={key for path in paths for key in path}
    if reachable!=set(scenes) or {path[-1] for path in paths}!=set(canon['endings']):reject('unreachable_ending','transitions','All scenes and exactly the two required endings must be reachable')
    for ending in canon['endings']:
        if edges[ending]:reject('ending_outgoing',ending,'Endings cannot lead to another scene')
    ending_texts=[scenes[key]['text'].strip() for key in canon['endings'] if key in scenes]
    if len(ending_texts)!=len(set(ending_texts)):reject('identical_endings','endings','Ending prose must not be identical')
    for path in paths:
        known={key:set(row['initial_clues']) for key,row in characters.items()}
        private={key:set(row['initial_secrets']) for key,row in characters.items()}
        introduced=set()
        for key in path:
            scene=scenes[key];pov=scene['pov']
            if pov not in known:continue
            for clue in scene['learns_clues']:
                if clue not in clues or dict(scene_id=key,character_id=pov) not in clues[clue]['discoveries']:reject('illegal_discovery',key+'.learns_clues',f"Character {pov} cannot discover clue {clue} in {key}; allowed discoveries: "+json.dumps(clues.get(clue,{}).get('discoveries',[])))
                else:known[pov].add(clue);introduced.add(clue)
            missing=set(scene['uses_clues'])-known[pov]
            if missing:reject('unknown_clue',key+'.uses_clues',f"Character {pov} has not learned "+json.dumps(sorted(missing))+'; current character knowledge: '+json.dumps(sorted(known[pov])))
            for secret in scene['learns_secrets']:
                if secret not in secrets:reject('unknown_secret',key+'.learns_secrets','Unknown secret '+secret)
                elif scene['minute']<secrets[secret]['earliest_minute'] or not set(secrets[secret]['requires_clues'])<=known[pov]:reject('early_secret',key+'.learns_secrets',f"Secret {secret} for {pov}: minute {scene['minute']} must be at least {secrets[secret]['earliest_minute']}; missing character clues: "+json.dumps(sorted(set(secrets[secret]['requires_clues'])-known[pov])))
                else:private[pov].add(secret)
            missing=set(scene['uses_secrets'])-private[pov]
            if missing:reject('knowledge_leak',key+'.uses_secrets',f"Character {pov} does not know secrets "+json.dumps(sorted(missing)))
            absent=set(scene['payoffs'])-introduced
            if absent:
                reject('invented_payoff',key+'.payoffs','payoffs accepts introduced clue IDs only, never secret IDs. Remove '+json.dumps(sorted(absent))+ '; introduced clue IDs on this path are '+json.dumps(sorted(introduced)))
        ending=scenes[path[-1]]
        if not introduced<=set(ending['payoffs']):reject('unresolved_clue',path[-1]+'.payoffs','Missing payoff for introduced clues '+json.dumps(sorted(introduced-set(ending['payoffs']))))
    return dict(status='FAIL' if issues else 'PASS',violations=issues,characters=size,paths=paths,
        semantic_status='NOT_VERIFIED',human_review_required=True)


def initial(canon,stage):
    if stage=='canon':return dict(canon=deepcopy(canon))
    if stage=='style':return dict(rubric=deepcopy(canon['editorial_rubric']))
    return {key:deepcopy(canon[key]) for key in ('scene_slots','transitions','entry','endings')}


class StoryVerifier:
    def __init__(self,service):self.s=service

    def invoke(self,context):
        stage=context.arguments['parameters']['stage'];ref=context.arguments['artifact']
        with self.s.db.connect() as c:
            task,value,dependencies,docs,keys=stage_receipts(self.s,c,ref,stage)
            canon=loads(c.execute('SELECT input FROM invocations WHERE id=%s',(task['project_id'],)).fetchone()[0])
            human_required=any(row['policy']['independent_review'] for row in loads(task['snapshot']).get('policies',[]))
        status='PASS';issues=[];method='declared_state_rules'
        if stage in ('canon','structure','style'):
            if value!=initial(canon,stage):issues=[dict(code='changed_commission',path=stage,message='Preserve the frozen commission and rubric')]
        elif stage=='draft':
            result=check_story(canon,value);status=result['status'];issues=result['violations']
        elif stage=='editor':
            method='model_assessment_and_reference_binding'
            if set(value['reviewed_artifact_ids'])!={row['artifact_id'] for row in dependencies}:issues=[dict(code='stale_editor_review',path='reviewed_artifact_ids',message='Review must cite current dependency versions')]
            else:
                facts=check_editor_facts(canon,docs['draft'],value);status=facts['status'];issues=facts['violations']
                if value['decision']=='INCONCLUSIVE' and not issues:status='INCONCLUSIVE'
                elif value['decision']!='APPROVE' or value['issues']:
                    issues.extend(dict(code='editor_rejected',path='issues['+str(index)+']',message=message) for index,message in enumerate(value['issues']))
                    if not issues:issues=[dict(code='editor_rejected',path='decision',message='Independent editor rejected the manuscript without an issue description')]
        elif stage=='release':
            result=check_story(canon,value['story']);status=result['status'];issues=list(result['violations'])
            if value['story']!=docs['draft']:issues.append(dict(code='changed_reviewed_story',path='story',message='Publication candidate changed the reviewed draft'))
            if docs['editor']['decision']!='APPROVE' or set(value['dependency_artifact_ids'])!={row['artifact_id'] for row in dependencies}:issues.append(dict(code='release_review',path='dependency_artifact_ids',message='Publication must bind the current approved draft and editor report'))
            if not human_required and not issues:status='INCONCLUSIVE'
        else:raise ValueError('Unknown stage')
        if issues:status='FAIL'
        code=issues[0]['code'] if issues else 'human_policy_missing' if stage=='release' and not human_required else 'editor_uncertain' if status=='INCONCLUSIVE' else 'declared_checks_passed'
        target='editor' if code in ('stale_editor_review','editor_fact_coverage','editor_fact_binding') else 'draft'
        return dict(status=status,checks=[dict(check_id=stage,status=status,code=code,node_key=keys.get(target,keys[stage]) if stage=='editor' else keys[stage],
            field_path=issues[0]['path'] if issues else None,message='; '.join(row['path']+': '+row['message'] for row in issues)[:4000] or code,
            repair='Fix the manuscript and declarations together. Do not lower the commission or claim unverified prose quality.')],
            evidence=dict(method=method,semantic_status='NOT_VERIFIED',human_review_required=True,violations=issues))
