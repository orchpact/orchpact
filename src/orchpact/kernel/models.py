from typing import Literal, Annotated
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def model_capabilities(capabilities):
    return [capability for capability in capabilities if not capability.startswith('tool:')]


class UnitInput(Strict):
    name: str = Field(min_length=1,max_length=100)
    purpose: str = Field(min_length=1,max_length=3000)
    parent_id: str | None = None
    model_id: str
    prompt: str = Field(default="",max_length=12000)
    crew: "CrewDeclaration | None" = None

class AgentInput(Strict):
    name: str = Field(min_length=1,max_length=100)
    role: str = Field(min_length=1,max_length=2000)
    prompt: str = Field(default="",max_length=12000)
    capabilities: list[str] = Field(default_factory=lambda:["text"],min_length=1,max_length=12)
    model_id: str | None = None

class CrewMember(AgentInput):
    key: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
    instruction: str = Field(min_length=1,max_length=6000)

class CrewDeclaration(Strict):
    process: Literal['sequential'] = 'sequential'
    members: list[CrewMember] = Field(min_length=1,max_length=6)

    @model_validator(mode='after')
    def valid_members(self):
        keys=[m.key for m in self.members]
        if len(keys)!=len(set(keys)):raise ValueError('团队岗位 key 不能重复')
        for member in self.members:
            tools=[capability for capability in member.capabilities if capability.startswith('tool:')]
            if 'text' not in member.capabilities or not set(model_capabilities(member.capabilities))<={'text','json','search'} or any(not re.fullmatch(r'tool:[A-Za-z][A-Za-z0-9_-]{0,63}',capability) for capability in tools):
                raise ValueError('团队岗位须声明 text，以及有效的模型能力或工具槽位授权')
        return self

UnitInput.model_rebuild()

class ProjectInput(Strict):
    name: str = Field(min_length=1,max_length=120)
    goal: str = Field(min_length=1,max_length=12000)
    root_unit_id: str
    request_limit: int = Field(default=64,ge=2,le=256)
    search_limit: int = Field(default=32,ge=0,le=100)

class ImageRequest(Strict):
    prompt: str = Field(min_length=1,max_length=16000)
    negative_prompt: str = Field(default='',max_length=3000)
    aspect_ratio: Literal['1:1','16:9','9:16','3:1','3:2','2:3','4:3','3:4'] = '1:1'
    model_id: str | None = Field(default=None,max_length=120)
    reference_artifact_ids: list[str] = Field(default_factory=list,max_length=6)

class ReplyInput(Strict):
    content: str = Field(min_length=1,max_length=8000)

class TextFile(Strict):
    path: str = Field(min_length=1,max_length=160)
    content: str = Field(max_length=200000)

class WorkOutput(Strict):
    summary: str = Field(min_length=1,max_length=4000)
    files: list[TextFile] = Field(min_length=1,max_length=20)

class Recruit(AgentInput):
    reason: str = Field(min_length=1,max_length=2000)

class PlanNode(Strict):
    title: str | None = Field(default=None,min_length=1,max_length=120)
    key: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
    kind: Literal['agent','tool','group'] | None = None
    parent: str | None = None
    unit_id: str | None = None
    staff: Recruit | None = None
    participants: list[str] = Field(default_factory=list,max_length=6)
    tool_ref: str | None = Field(default=None,pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    arguments: dict | None = None
    artifact_outputs: dict | None = None
    verification: dict | None = None
    description: str = Field(min_length=1,max_length=12000)
    expected: str = Field(min_length=1,max_length=3000)
    depends_on: list[str] = Field(default_factory=list,max_length=20)
    outputs: dict = Field(default_factory=dict,max_length=12)
    rules: list[dict] = Field(default_factory=list,max_length=40)
    exports: list[str] = Field(default_factory=list,max_length=12)
    gate: bool = False
    repair_from: list[str] = Field(default_factory=list,max_length=12)

class ExecutionPlan(Strict):
    nodes: list[PlanNode] = Field(min_length=1,max_length=40)
    result_node: str
    max_repairs: int = Field(default=2,ge=0,le=3)
    staffing: Literal['reuse_or_recruit','project'] = 'reuse_or_recruit'

class Assignment(Strict):
    unit_id: str | None = None
    agent: str | None = None
    description: str = Field(min_length=1,max_length=12000)
    expected: str = Field(min_length=1,max_length=3000)
    depends_on: list[str] = Field(default_factory=list,max_length=12)

class Message(Strict):
    to_task: str
    kind: Literal['question','reply','progress']
    content: str = Field(min_length=1,max_length=8000)
    reply_to: str | None = None

class Review(Strict):
    artifact_id: str | None = Field(default=None,max_length=120)
    task_id: str
    verdict: Literal['accept','rework','waive']
    feedback: str = Field(min_length=1,max_length=4000)

class Decision(Strict):
    action: Literal['plan','wait','deliver','accept','reject']
    summary: str = Field(min_length=1,max_length=4000)
    recruits: list[Recruit] = Field(default_factory=list,max_length=6)
    assignments: list[Assignment] = Field(default_factory=list,max_length=6)
    messages: list[Message] = Field(default_factory=list,max_length=6)
    reviews: list[Review] = Field(default_factory=list,max_length=48)
    result: str = Field(default="",max_length=20000,description='action=deliver时必须提供非空的交付结果说明；summary、files和source_artifact_id不能替代result。其他动作可省略。')
    files: list[TextFile] = Field(default_factory=list,max_length=19)
    source_artifact_id: str | None = Field(default=None,max_length=120)
    execution_plan: ExecutionPlan | None = None

    @model_validator(mode='after')
    def valid_delivery(self):
        if self.action=='deliver' and not self.result.strip():
            raise ValueError('action=deliver必须填写非空result（交付结果说明）；summary、files或source_artifact_id不能替代result')
        return self

class PlanningDecision(Decision):
    action: Literal['plan','wait','deliver']

class IntakeDecision(Decision):
    action: Literal['accept','reject','wait']

class LayoutInput(Strict):
    positions: dict[str, Annotated[list[float], Field(min_length=2,max_length=2)]] = Field(default_factory=dict,max_length=64)


from dataclasses import dataclass

@dataclass(frozen=True)
class ExecutionContext:
    subject: str
    workspace_id: str
    application_id: str | None = None
    user_id: str | None = None
    subject_type: str = "user"
    project_id: str | None = None
    admin: bool = False
    application_key_id: str | None = None
    client: str = "web"

class Command(Strict):
    key: str = Field(min_length=1, max_length=120)
    action: Literal["plan", "amend", "decide", "publish", "review", "retry", "cancel", "reply"]
    revision: int | None = Field(default=None, ge=1)
    payload: dict = Field(default_factory=dict)

class MandateInput(Strict):
    subject: str = Field(min_length=1, max_length=120)
    actions: list[str] = Field(min_length=1, max_length=16)
    capabilities: list[str] = Field(default_factory=list, max_length=8)
    expires: float = Field(allow_inf_nan=False)
    parent_id: str | None = None
    budget_points: int | None = Field(default=None, ge=0)

class ReviewInput(Strict):
    artifact_id: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verdict: Literal["accept", "rework", "waive"]
    feedback: str = Field(min_length=1, max_length=4000)

class GovernancePolicy(Strict):
    input_schema: dict = Field(default_factory=dict)
    leader_outputs: dict[str, dict] = Field(default_factory=dict)
    max_output_bytes: int | None = Field(default=None, ge=1, le=10 * 1024 * 1024)
    budget_points: int | None = Field(default=None, ge=0)
    independent_review: bool = False

class TemplatePreviewInput(Strict):
    parameters: dict
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
