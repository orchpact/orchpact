"""Check declared narrative state. Prose semantics always need editorial/human review."""
from collections import defaultdict
import json
from pathlib import Path

from orchpact.kernel.contracts import validate

HERE=Path(__file__).resolve().parent


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
    operation=json.loads((HERE/'workflow.json').read_text())['operations']['write']
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


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=HERE/'input.json')
    parser.add_argument('--candidate',type=Path,required=True)
    args=parser.parse_args();result=check_story(json.loads(args.input.read_text()),json.loads(args.candidate.read_text()))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='PASS' else 2 if result['status']=='FAIL' else 3)
