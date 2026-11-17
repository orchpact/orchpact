"""Compile reusable team declarations into immutable execution rosters."""
from ..adapters.database import dumps
from ..kernel.models import CrewDeclaration


def freeze_team(settings, unit, participants=None):
    declaration=unit.get('crew')
    if not declaration:return []
    team=CrewDeclaration.model_validate(declaration).model_dump()
    wanted=participants or [member['key'] for member in team['members']]
    by_key={member['key']:member for member in team['members']}
    if len(wanted)!=len(set(wanted)) or not set(wanted)<=set(by_key):
        raise ValueError('参与岗位不属于本团队声明')
    roster=[]
    for key in wanted:
        member=dict(by_key[key])
        profile=settings.choose(member['capabilities'],member.get('model_id') or unit['model_id'])
        member['capabilities']=dumps(member['capabilities'])
        roster.append(dict(key=key,agent=member,profile=profile,instruction=member['instruction']))
    return roster
