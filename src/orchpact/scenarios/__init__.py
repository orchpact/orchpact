"""The two packaged walkthroughs, including their local read-only verifiers."""
from dataclasses import dataclass

from .assets import read_asset, workflow
from .fulfillment import FulfillmentVerifier
from .mystery import StoryVerifier
from ..adapters.configuration import tool_policy
from ..kernel.verification import verifier_metadata
from ..kernel.workflows import validate_definition


@dataclass(frozen=True)
class Example:
    folder: str
    name: str
    description: str
    highlights: tuple[str, ...]
    verifier: type
    human_review: bool = False

    def definition(self):
        return validate_definition(workflow(self.folder))

    def sample(self):
        return read_asset(self.folder, 'input.json')

    @property
    def provider_id(self):
        return 'tool:example-' + self.folder

    def provider(self, definition):
        interface = definition['resources']['tools']['verifier']
        operation = next(iter(definition['operations'].values()))
        stages = [node['key'] for node in operation['execution_plan']['nodes']]
        return dict(
            id=self.provider_id, category='tool', label=self.name + ' · 本地校验器',
            enabled=True, adapter='builtin', base_url='', model='',
            priority=100, search=False, thinking_disabled=False, image_options={},
            pricing=dict(unit='request', points=0, unit_size=1),
            tool_interface=interface['interface'], tool_interface_version=interface['version'],
            implementation_version='1.0.0',
            tool_policy=tool_policy(dict(workflow_ids=[definition['id']], max_concurrency=1)),
            tool_verifier=verifier_metadata(dict(checks=stages, media_types=['application/json'],
                trust_level='trusted', environment='isolated-'+self.folder+'-python-v1')),
        )


EXAMPLES = {example.folder: example for example in (
    Example('fulfillment', '多仓订单履约',
            '为 3 座仓库和 8 笔订单制定发运计划，校验库存、承运容量、交期和预算。',
            ('冻结订单、库存与线路', '分配发运并核算成本', '独立复核与最终校验'),
            FulfillmentVerifier),
    Example('mystery-story', '闭馆后的第七声钟响',
            '按既定人物、线索和双结局创作互动悬疑故事，经过独立编辑与人工验收。',
            ('冻结设定、分支与风格', '写作及核对线索', '独立编辑与人工验收'),
            StoryVerifier, human_review=True),
)}


def verifiers(service):
    return {example.provider(example.definition())['tool_interface']: example.verifier(service)
            for example in EXAMPLES.values()}
