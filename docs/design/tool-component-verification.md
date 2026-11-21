# 工具、组件与可信验证：现状和变更设计

> 状态：阶段 0 设计基线及阶段 1–8 实现记录。工具、确定性节点、受控 Artifact、可信 Verifier/Evidence/Acceptance、局部返工、编译期组件、固定模板与参数表单已经实现；MCP 和独立参考应用仍未实现。
>
> 源码基线：`7f4493a29fa18822ea0bc3aabf32ebda1ecf3146`。实施后续阶段前须重新核对目标分支；内部 Python 模块和数据库表不是稳定扩展 API，公开变更以 `docs/COMPATIBILITY.md` 为准。

## 1. 范围与决策摘要

本设计覆盖工具声明与绑定、Tool Runtime、Agent 工具调用、确定性工具节点、Artifact 导入、Verifier/Evidence/Acceptance、局部返工、编译期组件和参数表单。文档交付领域实现放在独立参考应用，不进入 Core。

阶段 0 固定以下决策：

1. 工具逻辑声明加入 `resources.tools`，物理绑定加入现有安装 bindings；二者不混入模型 `capabilities`。
2. 工具节点使用显式 `kind: "tool"`，不新增平行 `tool` 节点对象，也不复用组织汇总的隐式语义。
3. 旧节点省略 `kind` 时完全沿用现有 `staff`/`participants`/组织节点判断；阶段 4 的 `outputs` 仍是 JSON Schema，阶段 5 的二进制扩展须使用独立 `artifact_outputs`，不能复用同一路径隐式切换含义。
4. 工具绑定沿用应用修订和 Workspace 显式安装边界；不能绕过当前“同一应用修订不得只改资源绑定”的规则。
5. 通用外部工具复用 `requests`、credit policy、钱包预留/结算和原付款人。首版只支持可信配置中的按次内部积分价格；未知外部费用不估算、不静默记零。
6. `tool_call_id` 是外部工具调用标识，与现有 run 的 `invocations.id` 不同。`agent_steps` 继续记录 CrewAI 行动，`tool_invocations` 记录外部副作用与恢复事实。
7. 现有 Artifact 存储、文件限制和授权下载继续复用。阶段 5 的根操作通过显式 `output_kind: "artifact"` 交付二进制，不靠文件扩展名隐式切换协议。
8. 组件只在发布前展开；`source_map` 是独立编译报告，不进入严格字段校验的工作流定义。

## 2. 当前实现调用链

### 2.1 发布、应用绑定与 Workspace 安装

| 顺序 | 实现入口 | 当前事实 | 工具扩展点 |
| --- | --- | --- | --- |
| 1 | `kernel/workflows.py::validate_definition()` | 严格校验 definition allowlist、资源模型槽位、团队和操作；最终调用 `validate_package(materialize(...))` | 增加 `resources.tools` 的纯结构校验，但不向旧定义回填空工具表 |
| 2 | `services/workflows.py::WorkflowCatalog.register()` | 校验管理员、目录 revision，追加不可覆盖的 `workflow_versions`，保存 `definition_digest()` | 新工具字段进入定义摘要；发布仍不修改应用或安装 |
| 3 | `services/workflows.py::WorkflowCatalog.bind()` | 应用固定 `workflow_id + workflow_revision`，应用 binding revision 独立递增 | 不加入工具物理配置，也不自动安装 |
| 4 | `kernel/workflows.py::bind_definition()` | 模型绑定恰好覆盖槽位，并调用 `Settings.choose()` 检查能力 | 扩展为 `{"models":...,"tools":...}`；旧无工具定义不要求空 `tools` 字段 |
| 5 | `kernel/workflows.py::materialize()` | 将模型槽位替换为实例 `model_id`，移除可移植 `format/resources` | 工具节点保留逻辑 `tool_ref`；物理配置存内部绑定/快照，不写入导出定义 |
| 6 | `services/platform.py::prepare_package()` | 生成安装 manifest，`_workflow` 保存来源、应用修订和模型 bindings | 在服务端解析并冻结工具安全配置摘要和运行时记录；输入 bindings 不携带真实秘密 |
| 7 | `preview_package()` | 只读预览，检查应用修订、当前安装和绑定冲突 | 工具预览也只读，返回安全的槽位/实现摘要，不返回凭据引用正文 |
| 8 | `install_package()` | 检查 `expected_revision`、`binding_revision`，创建独立组织实例并保存安装 manifest | 工具槽位缺失、额外绑定或策略越界时整体回滚；更新仍需新应用修订和显式安装 |

当前 `prepare_package()` / `install_package()` 会拒绝同一应用修订上的模型改绑。工具绑定必须沿用这一规则：即使 workflow revision 不变，也先创建新的应用 binding revision，再由目标 Workspace 显式预览和升级。其他 Workspace 不自动跟随。

### 2.2 发起 run 与冻结工作项

| 顺序 | 实现入口 | 当前事实 | 工具扩展点 |
| --- | --- | --- | --- |
| 1 | `services/platform.py::invoke()` | 检查应用、Workspace、幂等键、输入 Schema 和付款人余额；保存 `invocations.binding` | 在 run 内固定工具安全绑定摘要及实际运行时记录，不能只保存可变的当前配置 ID |
| 2 | `new_task()` | `freeze_team()` 固定参与岗位；保存 unit/agent/profile/prompt/policies、CrewAI 版本和 attempt 限额 | 为需要工具的节点复制 run 中冻结的非秘密工具配置和凭据引用；不解析出秘密明文 |
| 3 | `install_plan()` | `validate_plan()` 后创建统一 `tasks/dependencies` 图，保存计划和 revision | 工具节点仍生成普通平台工作项，不建立另一张执行图 |
| 4 | `claim()` | 只有依赖 `completed + accepted` 才可领取；生成 attempt，执行项目和工作项生命周期上限 | 工具工作项使用同一领取边界；未知副作用不得因此自动再次领取 |
| 5 | `context()` | 读取当前依赖 Artifact，生成有界摘录并构造任务执行身份 | 参数映射只引用当前授权依赖；二进制不以摘录形式注入模型 |

`tasks.snapshot` 是工作项执行配置的事实来源，明确重试通过 `invalidate()` 保留原工作项快照并创建新 attempt。后续应用改绑、Workspace 升级、工具配置轮换都不能回写旧 run 或旧工作项。

### 2.3 执行、交付、验收与恢复

1. `services/runner.py::Runner.step()` 领取工作项并调用 `CrewAIExecutor.execute()`；异常经 `Platform.fail()` 进入现有状态机。
2. `services/executor.py` 的文本路径调用 `Providers.agent_json()`，再进入 `adapters/crewai.py::run_crew()`；图片路径直接调用平台图片提供者。
3. `adapters/crewai.py` 每次启动按 roster 创建新的 Crew、Task、Agent。`PlatformLLM` 将模型 JSON Action 转成 CrewAI 协议，`ScopedTool` 执行注册的 `BaseTool`，`AgentSteps` 在行动前后授权、计数和留证。
4. 当前 `read_artifact` 只允许当前依赖产物，`search_evidence` 要求岗位自身具有 `search` 能力。规划和接收阶段 `allow_tools=False`。
5. 无 `staff`/`participants` 的组织节点不进入 CrewAI；`reconcile()` 在下级验收后原样汇总文件，attempt 的 `model_id` 为 `deterministic`。
6. `complete_work()` 校验当前 attempt 和执行主体，调用 `publish()`。`publish()` 依次执行 `DeliveryContracts.check()`、治理输出限制和 `Artifacts.publish()`，然后更新工作项及 attempt。
7. `reconcile()` 对普通节点尝试自动验收，对 gate 读取审校报告并执行有界返工；需要独立审校时拒绝自动接受。`review()` 绑定当前 Artifact ID、SHA-256 和 generation。
8. `invalidate()` 使目标、下游及受影响上级汇总节点失效，清除任务当前 Artifact 引用并增加 generation；历史 Artifact 和验收记录不覆盖。
9. `Runner.recover()`、`recover_steps()` 和 `Providers.recover_receipts()` 先恢复已有证据与结算，再把未确认执行标为 unknown/interrupted；不会恢复 CrewAI 内存循环或重发未知请求。

工具执行器只能作为 `Runner` 已领取工作项的一个分派分支。工具最终交付仍调用 `complete_work()/publish()`，工具 HTTP 成功、验证执行完成和平台验收不得合并成一个状态。

## 3. 工具声明、绑定和快照协议

### 3.1 逻辑声明

`resources.tools` 的 key 是工作流内稳定逻辑槽位。声明至少包含：

- `interface` 和作者接口 `version`；
- 有界 `input_schema` / `output_schema`；
- `effect`: `read | create_artifact | external_write`；
- 超时、最大输出字节数、重试和幂等声明；
- 所需权限、Artifact 引用规则和输出契约。

定义不包含 endpoint URL、实际凭据、Workspace、应用身份或部署开关。结构校验拒绝秘密字段，但不声称能扫描提示词或业务 Schema 中的所有秘密文本。

工具 Schema 使用独立的有界校验器，复用 `kernel/contracts.py` 的 JSON Schema 基础检查，并增加深度、节点数、引用和序列化大小限制。不能为加强工具 Schema 而直接收紧既有业务 Schema。

### 3.2 物理绑定与可信配置

阶段 1 的安装 body 为：

```json
{
  "models": {"reasoning": "local-model-profile"},
  "tools": {
    "document_render": {
      "endpoint_ref": "trusted-tool-provider-id",
      "implementation_version": "1.2.3"
    }
  }
}
```

绑定值只选择管理员登记的工具提供者和实现版本；接口、真实 URL、网络范围、应用/Workspace 策略和凭据由服务端可信配置给出。`ProviderConfiguration` 的 `category=tool` 记录复用 `secret()` 和只写秘密文件，不另建凭据仓库或动态 Python 插件 Registry。工具 provider 不使用环境变量凭据，避免历史快照依赖当前进程环境。

安装时服务端冻结：

- `_workflow.bindings.tools`：原始 endpoint/实现选择，用于既有 revision 与 binding conflict；
- `_workflow.tool_bindings`：adapter 类型、接口/实现版本、endpoint_ref 和非秘密配置摘要；
- run 的 `invocations.binding.tools`：逻辑定义、安全绑定及冻结 provider 记录，仅供内部执行；
- `tasks.snapshot.tools`：从 run 复制的实际配置和不可变凭据引用，明确重试继续使用。

公开 definition 导出不包含安装数据。安装预览和 run/API 只暴露安全来源与绑定摘要，不返回 URL、provider 记录、秘密明文或 `_secret_ref`。阶段 2 已增加内部 adapter 调用和工具请求/费用；声明本身仍不会自动注入 CrewAI 或发起调用。

### 3.3 快照与配置轮换

- 安装 manifest 固定该 Workspace/应用修订选择的工具绑定。
- 新 run 重新核对当前 provider 的安全配置摘要；语义配置变化会拒绝沿用旧安装，要求新应用修订和显式升级。
- 已受理 run 和工作项不读取当前配置替换旧快照，明确重试沿用原记录。
- 仅托管凭据轮换不改变安全摘要：新 run 使用新引用，旧 run 和重试继续解析旧引用。

旧定义省略 `resources.tools` 时，校验结果、发布摘要和安装 bindings 保持原样。不得自动加入 `tools: {}`、`kind: null`、`tool_limit: 0` 或 `output_kind: "json"` 来改变旧定义/导出摘要；运行时代码用缺省读取兼容旧数据。

## 4. 工具节点与根产物协议（阶段 4 已实现）

### 4.1 节点判别与 JSON 输出

可选 `kind: agent | tool | group` 已实现以下互斥规则：

| 节点 | 必需字段 | 禁止或兼容规则 |
| --- | --- | --- |
| 省略 `kind` | 沿用原字段 | 有 `staff`/`participants` 为 Agent；否则为组织汇总，旧定义摘要和行为不变 |
| `kind=agent` | `staff` 或 `participants`、JSON `outputs` | 不允许 `tool_ref`、`arguments` |
| `kind=tool` | `tool_ref`、有界对象 `arguments`、JSON `outputs` | 不允许 `staff`、`participants`、组织 `exports`、Agent gate 或 repair_from |
| `kind=group` | 至少一个下级及合法 `exports` | 不允许执行角色、工具字段或输出 Schema |

阶段 4 只开放 `effect=read` 和 `external_write` 的 JSON 工具节点。`create_artifact` 节点、`artifact_outputs` 和 `verification` 在阶段 5 有真实导入与验证实现前仍被拒绝，不能仅声明后自动验收。工具结果统一为 `{"files":{...}}`；输出 Schema 必须声明 `files` 对象并要求节点的全部文件，现有 `DeliveryContracts` 再校验精确文件集、各文件 Schema 和 rules。工具调用 completed 不等于交付 accepted。

### 4.2 参数引用与执行分派

参数只允许字面 JSON 和三种固定引用：

```json
{"$ref":"input","path":"project.value"}
{"$ref":"artifact","node":"facts","file":"facts.json"}
{"$ref":"json","node":"facts","file":"facts.json","path":"value"}
```

`artifact` 返回当前已验收依赖的 Artifact ID；`json` 从指定 Artifact 文件读取完整 JSON，不使用 Agent 的有界摘录。节点必须是本节点明确声明的直接依赖，组织节点可引用其唯一 `exports`。发布/安装检查引用结构、深度、依赖、声明文件和可静态判断的类型；运行时重新核对当前 accepted 任务、Artifact 所有权、文件摘要和完整工具输入 Schema。没有表达式、Python、环境变量或任意字符串插值。

实现贯穿 `PlanNode`、definition allowlist、`validate_plan()/packages`、`prepare/preview/install_package()`、`install_plan()/new_task()/claim()/context()/amend()`、`ToolNodeExecutor`、`ToolRuntime.invoke_node()` 和 `DeliveryContracts`。工具工作项使用 `executor.kind=tool`、`profile.id=deterministic`，attempt 同样为 deterministic，只冻结本节点槽位和最小授权；不创建 CrewAI Crew/Task/Agent、crew_run、agent_step 或模型请求。

节点幂等键绑定工作项、槽位和规范化参数，completed 跨 attempt 只读回私有原结果，不新增费用。UNKNOWN/started 永不自动重放；只有 `effect=read + retry_policy=safe_only` 的明确 failed 调用可在用户显式重试后以新 generation 幂等记录调用，历史记录不覆盖。

### 4.3 纯工具工作流

阶段 4 正式支持无模型纯工具工作流。定义不含动态 operation、Agent、团队或显式模型使用时，预览和安装不再强制选择文本模型；unit 的内部空 `model_id` 不导出为模型槽位。纯工具根协调工作项也保存确定性快照，安装计划后仍转为 group，不被领取。混合工作流仅 Agent/动态路径选模，旧定义继续保存原模型快照。

### 4.4 根产物扩展（阶段 5 已实现）

省略 `output_kind` 时 operation 继续使用 `.json output_file + output_schema`，`Platform.result()` 解析 JSON。显式 `output_kind="artifact"` 改用独立 `artifact_output` 媒体/大小契约，最终节点 `artifact_outputs` 必须一致，不能同时声明 `output_schema`。

二进制 run 的根 `artifact_id` 指向当前已验收根 Artifact，继续使用授权下载接口，API `result` 保持 null，不把字节伪装成 JSON 字符串。根汇总的新 Artifact 也执行 verification，不只验证最终子节点。

## 5. Tool Runtime、账务与恢复边界（已实现）

### 5.1 单一调用流程

`services/tools.py` 持有统一 Runtime 和代码内静态 Registry，`adapters/tools.py` 实现 HTTPS 与静态 builtin 传输。Registry 不从工作流加载 Python 对象，不提供 shell；MCP 尚无 SDK/transport，配置可读取但绑定明确拒绝。

`ToolRuntime.invoke()` 按以下顺序执行：

1. 检查停止标志、当前 attempt、墙钟期限、主体、`tool:<slot>` mandate 和 `required_permissions`。
2. 从工作项快照解析工具，校验安全配置摘要、当前 provider 是否停用、Workspace/应用策略和凭据引用。
3. 校验参数大小、JSON Schema 和当前依赖 Artifact 白名单；output 同样检查 Schema、大小和 Artifact 所有权。
4. 在同一数据库事务中检查 project + logical tool + idempotency key、attempt 工具步骤、项目 `tool_limit` 和 provider 并发，创建 `tool_invocations` 意图并复用 `Providers.reserve()` 冻结价格、预算和原付款人。
5. adapter 只执行一次；Runtime 不自动重试。响应通过校验后先原子写 `var/tool-invocations/<attempt>/<tool_call_id>.json`，再原子更新调用台账、requests、钱包和事件。
6. 无确认回执的 started 意图在 Runner 恢复时变为 unknown；匹配的 completed/failed 回执可以重复恢复且不重复结算。错配 ID、参数摘要或 binding 摘要的回执不能完成调用。
7. external_write 的断连、超时、5xx、重定向和不可信响应为 unknown；明确拒绝才是 failed。重复幂等请求和 Runner 恢复不会重放 started/failed/unknown；明确 retry 同样阻断这些状态，唯一例外是阶段 4 `read + safe_only` 节点的明确 failed 可由人工显式重试新增记录。
8. `POST /api/v1/tool-invocations/{id}/reconcile` 只调用 adapter 的只读 lookup。通用 HTTP 无标准 lookup，必须人工对账；lookup 不创建新的 tool_call_id/request，也不改变工作项验收状态。

`read_artifact/search_evidence` 仍走现有 CrewAI 专用路径，没有迁入通用 Runtime，不增加双重费用。阶段 3 已由 `prepare_role()` 按岗位授权注入通用 Runtime；阶段 4 的 `invoke_node()` 复用同一 `invoke()` 流程，以工作项/槽位/参数派生节点幂等键，completed 优先回读，UNKNOWN/started 阻断，只有 safe_only 只读明确失败可在人工重试时新增 generation 记录。

### 5.2 计费与额度

- 价格由管理员可信 provider 配置决定，不由工作流作者或 Agent 提交；首版必须有 `unit=request` 的内部积分价格。
- 明确的 `points=0` 会结算为 0，pricing 缺失在任何 adapter 调用前拒绝，二者不混淆。动态供应商账单不在首版自动结算范围。
- operation 可显式声明 `tool_limit`（0–64），旧定义不回填字段、旧项目缺省 0；attempt 同时受快照 `tool_steps` 限制，provider 另有 `max_concurrency`。
- `requests.kind="tool"`，`model_id` 使用 `tool:<logical-slot>`；usage 增加 `tool_requests/tool_points`。completed/failed 按冻结价格结算，unknown 保持 request unknown 和钱包 held，人工核定不伪造结果。
- 私有回执保存参数和结果，run `tool_invocations` 只返回元数据和摘要。当前 builtin 也创建内部 request 台账以保留统一价格与预算事实，不声称存在供应商请求。

### 5.3 安全实现与限制

HTTPS adapter 对初始 URL 和每一跳重定向执行精确 host/port allowlist 与全部 DNS 地址检查。默认拒绝非公网地址；`allow_private=true` 仍需管理员明确配置。实际 socket 直接连接已检查 IP，TLS 使用原域名做 SNI/hostname 校验，避免检查与连接之间的重绑定窗口。禁用环境代理，跨主机跳转不带 Authorization，external_write 不跟随跳转。

响应采用有界分块读取，检查 Content-Length 和实际字节，并应用连接/TLS/read deadline。系统 DNS 解析自身的超时依赖部署环境；合成 DNS/连接测试不证明真实公网 TLS 或目标服务安全。工具输出只是数据，不会提高指令优先级。已有依赖 Artifact 引用继续受白名单约束，新文件只能经阶段 5 的受控导入和媒体检查发布。

### 5.4 CrewAI Agent Tool Call（阶段 3 已实现）

岗位以 `capabilities` 中的 `tool:<槽位>` 显式授予工具；`kernel/models.py::model_capabilities()` 和 `Settings.choose()` 在模型匹配时排除这类授权，工作流校验要求槽位必须存在于 `resources.tools`。因此不需要给模型 profile 伪造工具能力，旧角色也不会自动获得新工具。

`prepare_role()` 对每个岗位独立取冻结 `snapshot.tools` 与自身授权的交集。动态 Pydantic 参数模型调用同一 JSON Schema 校验，CrewAI 负责行动循环，`ScopedTool` 才调用 `ToolRuntime.invoke()`；`PlatformLLM.call()` 仅翻译模型选择，不执行工具。规划和接收继续 `allow_tools=False`，读取/搜索不重复计费。

Agent 幂等键是工作项、槽位和规范化参数的稳定摘要，不包含 attempt。首次执行的 agent_step_id 同时作为 tool_call_id；已完成重放或 UNKNOWN 阻断会产生新的行动证据，但其中记录原 tool_call_id。这样明确重试即使再次选择相同外部写，也只会命中原 UNKNOWN，不会再次执行。通用观察有界且包含真实 tool_call_id，异常经 `ExecutionAborted` 退出，不能成为框架自动重试的普通观察。

## 6. Artifact、Verifier 与 Acceptance 边界（阶段 5 已实现）

### 6.1 受控导入与媒体契约

`artifact_outputs` 独立于 JSON `outputs`，声明路径、media_type 和 max_bytes。工具只返回有界 base64/SHA-256/媒体条目，`services/tool_artifacts.py` 重新解码、校验摘要和内容，再复用 `encode_files()` 与 `Artifacts.publish()`；不接受宿主路径、链接或工具直接写正式目录。JSON/UTF-8、常见图片、PDF、ZIP 和 OOXML 有明确结构检查，其他媒体拒绝，基础检查不冒充渲染质量。

operation 的 `output_kind=artifact + artifact_output` 与最终节点契约一致，二进制 run 的 `result=null`，使用当前已验收根 artifact_id 和原授权下载。根汇总产生的新 Artifact 也需通过自己的验证策略。

### 6.2 可信 Verifier 与证据

管理员 `tool_verifier` 决定 checks、media_types、trust_level 和 environment，并进入配置摘要及冻结快照。`verification` 只引用只读、trusted 绑定，required_checks 全部阻断。平台生成候选身份和 contract_digest，Verifier 返回有界 `PASS|FAIL|INCONCLUSIVE` 与 checks；未知、缺失或矛盾结果不能通过。

`verification_evidence` 和 `var/verification-evidence/` 保存 Artifact ID/SHA-256/generation、契约与 Verifier 摘要、tool_call_id、checks 和 bundle_digest。`0003_verification_evidence` 同时覆盖新库和命名迁移。run 提供安全元数据，授权证据读取与 run 同权；数据库和全部配套文件成对恢复。

### 6.3 已解决的无死锁时序

采用“同工作项候选验证”而不是普通下游 Verifier 节点：

1. 契约通过后先发布候选 Artifact，task/attempt 保持 running、review pending，下游不能领取，人工 accept 也会被证据门槛拒绝。
2. `VerificationService` 在事务外调用 Tool Runtime，幂等键绑定当前 Artifact、契约、Verifier 与参数。组织和根候选由 Runner 同一分派处理，不重复汇总。
3. 证据落盘/落库后，最终化事务再次检查 task/attempt/Artifact/SHA/generation/contract。PASS 才完成并进入现有 reconcile/review；FAIL/INCONCLUSIVE 失败，不自动返工。
4. `review(accept)` 再要求当前对象的 PASS 证据。独立人审继续在其后适用，waive 仍只是 partial/waived，没有新增 accepted 布尔值。
5. 恢复先处理已有证据或 completed 回执，再标记剩余 interrupted；没有 completed 调用时不发新请求。UNKNOWN 不能通过换 attempt 或新 Artifact 重放。

`tests/test_verification.py` 已覆盖候选到调用、调用到证据、证据到最终化的崩溃点，以及 Agent、工具、根汇总和 API 隔离。

### 6.4 局部返工（阶段 6 已实现）

`Platform.repair()` 统一原 gate 与 Verifier FAIL 的返工入口，保持 `issues[].node_key` → `repair_from` → 当前节点的确定回退顺序。`CheckResult` 新增 node_key/artifact_path，只有确定性 FAIL 才调用；INCONCLUSIVE、UNKNOWN 和工具执行失败不进入自动返工。

失效范围包含受影响节点、下游和必要上级汇总，不包含未受影响 sibling。计划级 `max_repairs`、工作项 12 次和项目 96 次生命周期上限继续生效，历史 Artifact/证据不覆盖，新代次重新验证。`tests/test_repair.py` 覆盖单文档、共享事实、回退、额度和 INCONCLUSIVE。

## 7. 组件与模板边界

### 7.1 组件展开器（阶段 7 已实现）

`kernel/components.py` 是独立作者侧编译器，`orchpact component-expand` 离线读取 `orchpact-component-workflow` 宿主和本地 `orchpact-component` 文件。文件名与 `use` 必须固定为 `<component_id>@<version>`，实例同时固定 canonical SHA-256；没有 latest、远程加载或运行时 Registry。

组件用 `parameters_schema` 校验 `with`，仅替换完整 `{"$param":"name"}` 或带 `path` 的引用。`inputs` 声明必填 JSON/Artifact 端口，`outputs` 把命名端口映射到内部节点文件；占位节点通过 `ports` 把输出改为宿主路径。节点、unit、模型/工具槽位和 Verifier 引用使用完整 `<instance>__<key>` 命名空间，依赖、返工、结果节点、参数引用、工具输出 Schema 和组织 `exports` 一并重写。每个实例隔离资源，冲突直接拒绝。

递归最多 4 层、32 个实例，并检测循环；组件独立校验与最终宿主均复用 `validate_definition()`，继续受现有节点数、计划和定义大小限制。报告单独保存 `expander_version`、固定组件摘要、实例路径、原组件内部节点到最终节点的 `source_map` 和最终 `definition_digest()`，不进入严格工作流定义。

```sh
uv run orchpact component-expand author.json --components-dir ./components \
  --output expanded.json --report expanded.report.json
```

CLI 不加载 Settings，输出与报告必须是不同的新文件。Runtime、发布、应用绑定和 Workspace 安装接口不变；组件升级必须产生新展开定义并显式发布、绑定和安装。`tests/test_components.py` 覆盖双实例、嵌套/循环、参数与引用错误、Artifact/Verifier、组织 exports、深度/实例/节点/大小限制、冲突、摘要确定性和禁止覆盖。

### 7.2 固定模板与参数表单（阶段 8 已实现）

`kernel/templates.py` 保存代码内固定、带版本与 canonical SHA-256 的 `orchpact-workflow-template`，首版提供通用 `json-delivery`。`parameters_schema` 支持有界对象、字符串、数字、布尔、标量数组、默认值、说明和枚举；服务端递归应用默认值后复用 `validate()`、`substitute()` 和 `validate_definition()`。组件宿主也可在显式本地目录下复用阶段 7，不会在 HTTP 预览中猜测路径。

管理员通过 `GET /api/v1/workflow-templates` 读取目录和表单 Schema，通过 `POST /api/v1/workflow-templates/{template_id}/preview` 提交 `sha256 + parameters`。响应包含解析参数、规范定义、最终 SHA-256 和可选组件报告；接口只读，不创建工作流、应用、安装或执行。

`static/kernel/template-form.js` 生成原生控件、读取参数并映射服务端字段错误，不实现第二套 JSON Schema 或权限规则。管理台在参数变化时废弃旧预览，展示实际操作、固定节点、依赖、文件和完整 JSON。点击“使用此定义登记”仅把规范定义带入现有发布编辑器，保存仍显式调用 `POST /workflows`；应用绑定和 Workspace 安装继续独立执行。

模板不能填写真实模型、endpoint、Workspace、凭据或私有 Artifact ID。受控选择只表达模板预定义枚举和逻辑引用，物理绑定仍在现有安装页完成。首版没有自定义模板市场、远程模板、数组对象表单或拖拽编辑器。

## 8. 分阶段文件变更图

| 阶段 | 预计修改或新增 | 目的 |
| --- | --- | --- |
| 1 定义/绑定/快照 | `kernel/workflows.py`、`kernel/packages.py`、`kernel/contracts.py`、可新增 `kernel/tools.py`；`adapters/configuration.py`、`services/platform.py`、`interfaces/http.py`、`interfaces/client.py`、`static/kernel/app.js` | 纯逻辑工具 Schema、可信绑定、预览/安装、run 与工作项快照、导出清理 |
| 2 Runtime/账务/恢复 | 可新增 `services/tools.py`、`adapters/tools.py`；`adapters/providers.py`、`services/agent_steps.py`、`services/runner.py`、`credits.py`、`adapters/schema.py`、数据库迁移接入 | 统一调用、静态 adapter Registry、工具台账、限额、计费、回执和 unknown 恢复 |
| 3 Agent Tool Call | `adapters/crewai.py`、必要的 `services/tools.py` | 按岗位动态注入工具，保留 CrewAI 行动循环和现有 builtin 语义 |
| 4 确定性节点 | `kernel/models.py`、`kernel/workflows.py`、`kernel/plan.py`、`kernel/packages.py`、`services/platform.py`、`services/executor.py` 或统一分派器、`services/validation.py`、`services/runner.py` | 显式节点类型、参数映射、无 CrewAI 执行、计划变更与根输出兼容 |
| 5 Artifact/Verifier/Evidence | `kernel/files.py`、`adapters/artifacts.py`、可新增 `services/verification.py`、`services/platform.py`、`interfaces/http.py`、`adapters/schema.py` | 受控导入、二进制契约、验证证据和验收门槛 |
| 6 局部返工 | `services/platform.py` 及验证定位映射 | 复用现有失效传播与有界返工 |
| 7 组件 | 可新增 `kernel/components.py`、`cli.py`；编译器测试 | 发布前确定性展开，独立 source map |
| 8 模板/表单 | `static/kernel/app.js` 及必要的新原生 JS/CSS、模板编译/预览入口 | 参数表单和错误映射，不合并发布/绑定/安装 |
| 9 参考应用 | 独立参考应用/工具服务 | 领域工具和业务规则，不给 Core 增加 DOCX/PDF 业务依赖 |

任一公共协议阶段均须同步 `contracts/`、`docs/COMPATIBILITY.md`、相关工作流/API 文档和示例。数据库变更还须同步 `docs/POSTGRESQL.md` 与发行验证。

## 9. 兼容性与迁移要求

### 9.1 不得破坏的事实

- 无工具旧定义的规范化 JSON、SHA-256、发布/导出和安装行为不变。
- workflow revision、application binding revision、Workspace installed revision、run 来源和 work item/attempt 快照保持分离。
- 同一应用在同 Workspace 的不同资源选择、不同应用和不同 Workspace 的运行、Artifact、requests、回执和费用相互隔离。
- 团队声明可复用，每次实际执行创建新 Crew/Task/Agent；岗位不共享工具权限，attempt 限额不被刷新。
- 原付款人、价格快照、预留、幂等结算、真实回执和 unknown 语义不变。
- 现有 JSON `outputs`、gate、waive/partial、组织汇总、计划变更和局部返工行为不变。
- Artifact 文件数、大小、路径、哈希和下载授权不被静默放宽。

### 9.2 Schema 与数据迁移

阶段 1 优先使用 definition、安装 manifest 和 ProviderConfiguration 的 JSON 扩展，不为逻辑声明新增数据库表。必须验证旧记录缺少工具字段时无需回填。

阶段 2 起新增 `tool_invocations` 等表时：

1. 在 `adapters/schema.py::SCHEMA` 增加新实例完整 DDL；
2. 通过 `Database.migrate()` 的持锁命名迁移升级已有实例，不能只依赖 `CREATE TABLE IF NOT EXISTS`；
3. 若修改既有表，初始化和命名迁移必须分别包含完整变更；向 `SCHEMA` 加列不会升级旧库；
4. 迁移前生成 `pg_dump`，测试新库初始化、旧库升级、重复迁移、失败回滚和备份恢复；
5. 历史 run/task/request 不伪造工具记录或证据，旧结果按原协议读取；
6. Artifact、证据和回执文件与数据库成对备份恢复，回滚前评估新写入，不直接覆盖当前库。

阶段 5 已按候选验证时序增加 `verification_evidence` 和 `0003_verification_evidence`，新实例与命名迁移共用同一 DDL；迁移前备份、重复迁移及成对恢复均有测试。

## 10. 测试方案

### 10.1 阶段 0 复用基线

本阶段没有改变运行行为，不新增重复测试或空壳脚手架。现有覆盖如下：

| 现有测试 | 冻结的基线事实 |
| --- | --- |
| `tests/test_workflows.py` | 严格定义字段、槽位绑定、只读预览、独立 Workspace、旧 run/模型快照、导出清理 |
| `tests/test_workflow_catalog.py` | 不可变修订、应用固定引用、显式升级、签名应用间文件/请求/费用隔离 |
| `tests/test_team_crews.py` | 新 Crew/Agent、参与名单、团队交接、成员能力和模型快照、共享限额 |
| `tests/test_crewai_execution.py` | 真实 CrewAI 工具循环、参数拒绝、产物白名单、取消/撤销、步骤上限和恢复 |
| `tests/test_planned.py` | DAG、无模型组织汇总、失败显式重试、JSON 文件契约 |
| `tests/test_kernel.py` | Artifact 哈希验收、独立审校、定向返工、计划变更、治理快照 |
| `tests/test_runtime.py` | 原付款人、预留/结算、缺失用量、回执恢复、禁止自动重放 |
| `tests/test_platform.py`、`tests/test_security.py` | 文件/ZIP/篡改、数据库与文件成对恢复及实例边界 |
| `tests/test_concurrency.py`、`tests/test_wallets.py` | 并发领取、幂等和钱包一致性 |

标准基线命令：

```sh
uv sync --frozen
uv run orchpact workflow-check examples/workflows/department-review.json
uv run python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.cjs
git diff --check
```

PostgreSQL 只能来自 `ORCHPACT_PG_BIN` 临时集群，或具有 CREATEDB 的专用 `ORCHPACT_TEST_DATABASE_URL` 与匹配 `ORCHPACT_PG_DUMP`。合成传输不证明真实模型质量或外部工具安全性。

### 10.2 后续阶段新增测试

| 阶段 | 必须新增的确定性场景 |
| --- | --- |
| 1 | 旧定义摘要不变；工具槽位缺失/额外/错误版本；结构化秘密字段；跨应用/Workspace 绑定；预览只读；配置轮换下旧 run 与明确重试稳定 |
| 2 | 参数和响应 Schema、SSRF/重定向/DNS、输出过大、并发与额度、重复幂等、伪造回执、崩溃前后恢复、免费与未知费用区分、UNKNOWN 零重放 |
| 3 | 每个岗位独立工具集；同 Crew 不串权；未知名称和恶意观察；搜索不双重计费；规划/接收仍无工具；现有 CrewAI 测试全部保留 |
| 4 | 三类节点兼容；工具无 CrewAI 请求；组织汇总不回归；参数引用越界；单/多 JSON 输出；计划新增/修改；纯工具工作流无强制文本选模；completed/UNKNOWN/safe_only 恢复 |
| 5 | 路径穿越、符号链接、ZIP 炸弹、MIME 伪造、哈希/generation 不符；二进制与 JSON 双输出及二进制根下载；PASS/FAIL/INCONCLUSIVE；缺失/过期证据；验收 TOCTOU；候选到证据各崩溃点；waive 不冒充 completed |
| 6 | 单分支及上级汇总返工、sibling 不变、共享事实多分支失效、额度耗尽、UNKNOWN 不进入普通返工 |
| 7 | 双实例、嵌套、循环、参数错误、固定版本、规范摘要、source map 独立、旧 JSON 直接发布 |
| 8 | 表单与服务端 Schema 一致、错误映射、绑定越权拒绝、发布/绑定/安装不自动串联 |
| 9 | 本地 mock 文档服务 E2E、两套脱敏样本和保留集盲评；不访问真实付费服务，不用合成结果宣称文档质量 |

## 11. 未决事项与阶段出口

### 阶段 1 前已足够明确

- 使用 `resources.tools` 和现有安装 bindings；逻辑与物理配置分离。
- 沿用应用修订和 Workspace 显式安装，不新增静默可变绑定。
- 旧定义不回填新默认字段，摘要和导出不变。
- 可信工具配置优先复用 ProviderConfiguration 和秘密引用机制；具体公开字段在阶段 1 测试中冻结。

### 后续阶段阻断项

1. MCP SDK/transport 仍需独立评审，未评审前继续拒绝绑定。
2. 阶段 5 已实现 `artifact_outputs`、`output_kind=artifact`、不可变证据和无死锁候选验收时序；人工例外接受未新增，waive/partial 不得冒充完整接受。
3. 阶段 6 已把确定性 FAIL 映射到共享局部返工入口；INCONCLUSIVE/UNKNOWN 不进入普通自动返工。
4. 阶段 7 的编译报告由作者通过 `--report` 显式保存；阶段 8 已提供管理员只读模板目录/预览 API 和原生表单，不改变既有发布与安装协议。

### 阶段 0 出口条件

- 本文给出真实调用链、逐阶段文件修改图和复用边界。
- 基线测试按仓库标准实际运行，结果由当次交付报告如实记录。
- 未决 Schema、迁移和 Verifier 时序已显式列出。
- 本阶段不修改执行引擎、公共 JSON、数据库 Schema 或 Workspace 安装。

阶段 0 完成后等待确认，再进入阶段 1；不提前实现工具市场、远程 Worker、分布式调度或拖拽编辑器。
