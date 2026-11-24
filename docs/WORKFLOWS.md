# 工作流资产、空间安装与执行

Orchpact 是可自托管的声明式工作流执行服务。定义描述团队、岗位、阶段依赖、资源需求、交付和验收，CrewAI 执行团队内部协作。定义不依附某个消费应用；多个使用方可以引用同一资产与修订，同时保留各自的接入身份和执行边界。

## 三个独立动作

| 动作 | 标识与版本 | 作用 |
| --- | --- | --- |
| 发布定义 | workflow_id + workflow revision | 保存作者 version、SHA-256 和不可覆盖的定义 |
| 空间安装 | Workspace + workflow_id + installation revision | 固定工作流修订和本地模型/工具绑定 |
| 发起执行 | run ID | 固定输入、付款人、定义、资源和验收快照 |

发布不升级空间；显式安装升级不重写旧执行。相同 Workspace/工作流/幂等键跨升级仍回读原执行。工作流不再要求独立子应用、签名或网关。直接接口和历史兼容边界以 [直接运行工作流](APPLICATIONS.md) 为准。

## 声明如何成为一次团队执行

| 定义字段 | 含义 | 实例化边界 |
| --- | --- | --- |
| `resources.models` | 文本、JSON、搜索等模型能力需求 | Workspace 安装时解析本地模型绑定 |
| `resources.tools` | 逻辑工具接口、版本、Schema、效果与限额 | 安装时绑定可信 provider；岗位可经 CrewAI 动态调用，`kind=tool` 节点可直接确定性调用 |
| `units[].crew.members` | 稳定岗位 key、职责、指令、能力与模型槽位 | 工作项创建时冻结参与岗位和资源；实际启动时创建 CrewAI Agent |
| `execution_plan.nodes[].participants` | 本团队参与岗位及协作顺序 | 按声明创建 CrewAI Tasks，不能引用其他团队岗位 |
| `depends_on`、`outputs` | 跨团队依赖和文件契约 | 由 Orchpact 工作项图与不可变产物实现 |
| `gate`、`repair_from`、`rules` | 审校门禁、允许返工范围与确定性检查 | 由平台检查当前产物和推进有界返工 |

完整团队示例见 [department-review.json](../examples/workflows/department-review.json)。团队内部 notes 通过 CrewAI 上下文交接，跨团队依赖通过平台产物交接；团队结束只是候选输出，仍须通过交付契约和适用验收。每次实际启动创建新运行对象，工作流 JSON 不嵌入 Crew、Task、Python 回调或实例凭据。声明与实际对象的完整映射见 [执行团队设计](TEAM_EXECUTION.md)。

固定计划是当前推荐入口。缺省无计划时的自主规划、招募和动态分派保留为实验能力。执行身份不能提交 `amend`，旧的宽泛授权也不例外。获授权用户可以调整未开始的普通工作、增加节点；声明 `gate`、`verification`、`rules` 或冻结独立验收策略的节点不能取消或改写验收描述/期望，其输入依赖及包含它们的汇总路径不能取消。已有验收依赖不能移除，可增加依赖和更严格的检查。削弱验收的变更返回 `acceptance_locked`，应发布新定义并发起新执行，不原地修改历史契约。

节点 `accepted` 只表示通过该节点声明的检查及适用验收，不自动包含其他独立审校节点的结论。需要等待审校的下游必须在 `depends_on` 中同时声明数据来源和审校门；审校读取生产者候选，消费节点等待审校完成。平台不从自然语言推断这条依赖，也不自动改变已有定义的执行图。

### 将创作策略写入定义

[explicit-reference-report.json](../examples/workflows/explicit-reference-report.json) 将原始资料整理、来源选择、报告撰写声明为三个独立节点。`selection.json` 用 Schema 限制引用数量，用 `references` 检查来源 ID；成稿通过 `equals_dependency` 保留选择结果，用 `text_references` 检查正文引用闭包。每个节点的模型调用、费用、产物和失败都沿用普通运行记录。

执行器及模型适配器不再根据引用规则隐式启动来源选择、不动态收窄冻结的输出 Schema、不注入报告专用提示词。通用 JSON 协议修正、文件/业务规则检查及有界错误反馈继续保留。旧定义需要选源策略时，应参考示例发布新修订并显式升级安装；旧执行、回执和已保存产物不重写。示例只演示创作步骤与引用约束，不验证资料抄录、论断是否受来源支持或事实真伪，相关保证需要独立 Verifier 或人工验收。

## 定义与模型、工具资源

format 固定为 orchpact-workflow，id 是工作流资产标识，version 是作者版本；units 使用稳定 key，操作/计划使用 unit 别名。resources.models 声明能力槽位，unit、crew member、agent 和节点 staff 可通过 model 引用它。下面是无固定计划的最小定义；多岗位、跨团队审校示例使用上面的 department-review.json，更小的固定计划见 [evidence-review.json](../examples/workflows/evidence-review.json)。

```json
{"format":"orchpact-workflow","id":"shared-workflow","name":"Example","version":"1.0.0",
 "resources":{"models":{"reasoning":{"capabilities":["text","json"]}}},
 "units":[{"key":"entry","name":"处理组","purpose":"交付约定结果","model":"reasoning"}],
 "operations":{"create":{"unit":"entry","instruction":"交付 result.json","input_schema":{"type":"object"},"output_schema":{"type":"object"},"output_file":"result.json"}}}
```

本地模型绑定另存为 `{"models":{"reasoning":"local-profile-id"}}`。绑定必须恰好覆盖声明槽位，所选模型必须满足能力要求。未指定槽位的角色沿用能力选模机制；需要明确使用方模型时应声明 model。

`resources.tools` 声明逻辑工具槽位。每个工具必须给出 `interface`、固定 `version`、`input_schema`、`output_schema` 和 `effect`（`read/create_artifact/external_write`）；可设置 `description`、`timeout_ms`、`max_output_bytes`、`retry_policy`、`idempotency`、`required_permissions` 和 `artifact_contract`。Schema 只使用本地引用，工具定义不接受 URL、endpoint、凭据或秘密字段。`create_artifact` 必须声明 `artifact_contract`，其中可约束媒体类型、文件数和大小。

工具安装绑定与模型并列，但只选择管理员登记的提供者和明确实现版本：

```json
{"models":{"reasoning":"local-profile-id"},
 "tools":{"renderer":{"endpoint_ref":"tool:renderer","implementation_version":"1.0.0"}}}
```

安装器校验槽位恰好覆盖、接口/版本匹配和 Workspace/应用策略。公开预览与 run 的 `workflow.tool_bindings` 只含类型、版本、endpoint_ref 和配置摘要；真实 URL、provider 记录和秘密引用仅保存于内部 run/工作项快照。配置语义变化需要新应用修订和显式安装升级；托管凭据轮换不回写旧 run 或明确重试。

**当前已实现工具声明、绑定、快照、Tool Runtime、CrewAI 岗位动态调用、确定性工具节点、受控二进制导入和可信验证；MCP transport 尚未实现。** operation 只有显式设置 `tool_limit` 才能调用通用工具，旧定义缺省为 0。岗位动态调用还须在自己的 `capabilities` 中声明 `tool:<槽位>`，例如 `["text","json","tool:renderer"]`；该授权不是模型能力，不会加入模型槽位需求，也不会从其他岗位继承。无工具旧定义不会回填 `tools:{}`、`tool_limit` 或 `kind:null`，其摘要与安装行为不变。

### 确定性工具节点

`execution_plan.nodes[]` 可显式声明 `kind=agent|tool|group`。省略时保持旧语义；只有 `kind=tool` 使用 `tool_ref + arguments`，不创建 CrewAI Crew/Task/Agent，也不要求岗位 `capabilities` 或文本模型。工具节点必须声明 JSON `outputs`，不能与 `staff/participants/exports/gate/repair_from` 混用；`kind=group` 继续使用 `exports` 原样汇总。

```json
{"key":"render","kind":"tool","tool_ref":"renderer","depends_on":["facts"],
 "description":"Render the accepted facts","expected":"result.json",
 "arguments":{"data_artifact_id":{"$ref":"artifact","node":"facts","file":"facts.json"},
              "value":{"$ref":"json","node":"facts","file":"facts.json","path":"value"}},
 "outputs":{"result.json":{"type":"object"}}}
```

参数还可使用 `{"$ref":"input","path":"value"}` 读取 operation 输入。Artifact/JSON 引用必须指向直接 `depends_on` 的已验收节点及声明文件，组织节点可引用唯一 `exports`；运行时读取完整 JSON 并重新检查 Artifact 身份、权限和 Schema，不使用截断摘录，不执行表达式或代码。

工具节点可用 `outputs` 返回 JSON，也可用独立 `artifact_outputs` 声明二进制路径、`media_type` 和 `max_bytes`；二者路径不能重叠。`create_artifact` 必须声明产物输出。工具结果分别使用 `files` 和 `artifacts`，Artifact 条目严格为 `data_base64 + sha256 + media_type`：平台重新解码、核对摘要、大小、路径、媒体结构后才进入现有不可变存储。支持 JSON/UTF-8 文本、PNG/JPEG/WebP、PDF、ZIP 和 DOCX/XLSX/PPTX 的有界检查；其他媒体类型明确拒绝。ZIP 会检查链接、路径穿越、加密项、条目/解压限制和异常压缩比，这些检查不代表真实渲染或业务质量。

operation 可显式使用 `output_kind="artifact"`，以 `output_file + artifact_output` 声明二进制根契约并省略 `output_schema`。completed 时 `result` 为 null，根 `artifact_id` 仍经授权下载。省略 `output_kind` 的旧 JSON 定义行为与摘要不改变。

### 可信 Verifier 与验收

Agent、工具和组织节点可声明 `verification={"tool_ref":"verifier","required_checks":["structure"],"parameters":{...}}`；固定 operation 也可用同字段验证根 Artifact。Verifier 必须是只读工具，且管理员在 provider 的 `tool_verifier` 中显式配置 `trust_level="trusted"`、支持的 checks、媒体类型和执行环境。required_checks 全部为阻断项，工作流作者不能自行授予可信身份。

候选 Artifact 先固定 ID/SHA-256，task 保持 running、review pending，Verifier 在事务外读取平台构造的 `artifact + contract_digest + parameters`。Verifier 返回 `PASS|FAIL|INCONCLUSIVE` 和有界 checks；HTTP 成功不是 PASS，缺失/未知/矛盾检查为 INCONCLUSIVE。证据绑定 Artifact ID、摘要、generation、契约摘要、Verifier 版本与 tool_call_id，落库后才允许现有 `review()` 接受。waive 仍只是 partial/waived，恢复只回读已有 completed 验证回执，UNKNOWN 不因换 attempt 或 Artifact 重放。

调用前被拒绝的候选（例如 Verifier 提供者已停用）会标记为失败，原因记录在工作项和尝试中。正常停机拦下尚未发起的验证时，候选保持待验，交由重启恢复处理。只有实际调用记录可以关联验证证据。重启时按候选和冻结绑定核对已完成回执，缺失或无效回执的候选留给中断处理，其余候选继续离线恢复。恢复中才生成的根候选由正常调度继续验证。

确定性 FAIL 可使用 checks 中的 `node_key`、`artifact_path`、`field_path`、`check_id` 和 `repair` 进入现有定向返工：平台按有效上游 node_key、repair_from、当前节点的顺序选择，并沿依赖和必要上级汇总失效，独立 sibling 不变。INCONCLUSIVE、UNKNOWN 和工具执行失败不自动返工。`execution_plans.repairs/max_repairs` 继续限制总次数，旧 Artifact/证据不覆盖，新代次必须重新取得 PASS。

具体返工要求只加入被选中修正节点的任务说明。因上游版本变化而失效的下游节点重新读取最新依赖，不继承上游旧缺陷作为自己的未决要求，避免审校反复引用已经修复的旧正文。`plan.rework` 事件分别记录直接修正目标 `targets` 和全部失效节点 `affected`，历史验证证据仍保留。

当前代次的直接修正 Agent 通过 `repair_candidate` 获取本任务上一次不可变候选及 ID、SHA-256、原 attempt。它独立于已验收依赖 `deliveries`，标记 `accepted=false`；内容遵守同一交付摘录限额及总体上下文上限，截断时 `complete=false`。作者可在原稿上修正，避免仅凭失败摘要重写而丢失合规内容。下游重新审校不自动接收旧报告作为候选。此附加上下文也进入付费响应恢复的输入一致性检查，不能复用基于不同旧稿生成的响应。

工具工作项只冻结本槽位，attempt 的 `model_id` 为 `deterministic`。完全不含 Agent/团队/动态操作的纯工具定义可省略模型槽位与 unit model，安装和运行不强制选择文本模型。completed 结果跨明确重试幂等回读；UNKNOWN 不重放。只有 `retry_policy=safe_only` 的只读节点可在明确失败后由人工显式重试。

定义的结构字段拒绝实际 model_id、unit_id、账号、空间、绑定和部署开关。业务 Schema 与提示词内容不会被关键字删除，导出不是任意文本秘密扫描。工作流指纹包含定义 id，但与消费应用 id 无关。

```sh
uv run orchpact workflow-check examples/workflows/department-review.json
```

离线校验不加载实例配置、不连接数据库或调用模型。

## 编译期 JSON 组件

可复用组件是作者侧输入格式，不是运行时插件。宿主使用 `format="orchpact-component-workflow"`，组件文件使用 `format="orchpact-component"`，文件名与引用固定为 `<component_id>@<version>.json` / `<component_id>@<version>`。每个实例必须提供组件 canonical SHA-256；不支持 latest、URL、环境变量或秘密插值。

组件通过 `parameters_schema` 和实例 `with` 校验参数，只替换完整 `{"$param":"name"}` 或 `{"$param":"name","path":"nested"}` 对象。`inputs` 声明必填 JSON/Artifact 端口，实例用现有 `$ref=json/artifact` 绑定；`outputs` 声明内部节点文件，占位节点的 `ports` 将组件输出端口映射为宿主文件路径。宿主 `outputs` / `artifact_outputs` 契约必须与组件源文件一致。

```sh
uv run orchpact component-expand author.json \
  --components-dir ./components --output expanded.json --report expanded.report.json
```

该命令不加载 Settings、数据库、模型或工具，输出与报告必须是不同的新文件。组件可嵌套最多 4 层，每次展开最多 32 个实例；循环、摘要/版本错误、缺失/额外参数、非法引用、命名空间冲突及现有节点/大小超限均拒绝。每个组件实例的节点、unit、模型/工具与 Verifier 槽位独立命名空间化，文件输出、工具输出 Schema、依赖、返工、引用和组织 `exports` 同步改写。

展开结果仍是普通 `orchpact-workflow`，由现有 `validate_definition()` 权威校验；报告另含组件摘要、实例、原始内部节点 `source_map` 和最终 SHA-256，不进入定义或运行快照。发布后仍须独立执行应用绑定与 Workspace 显式安装，组件升级不会热更新历史执行。详细作者格式见 [设计文档](design/tool-component-verification.md#71-组件展开器阶段-7-已实现)。

## 固定模板与参数表单

管理台“工作流目录”提供固定模板选择和参数表单。首版内置通用 `json-delivery`，以 `parameters_schema` 描述字段标题、说明、默认值、必填、范围与枚举；支持嵌套对象、字符串、整数/数字、布尔和标量数组。服务端应用默认值并执行权威 JSON Schema 与工作流校验，浏览器不复制校验规则。

模板目录和预览均仅限管理员。预览请求必须携带目录返回的模板 SHA-256，响应包含解析后的参数、普通规范工作流、最终 SHA-256 和可选组件报告。页面展示操作、实际固定计划节点、依赖和交付文件；参数变化后旧预览失效。模板和参数错误保留输入，并尽量映射到具体字段。

“使用此定义登记”只将预览定义带入现有发布编辑器。确认保存才调用现有发布接口，不会自动创建应用绑定、安装 Workspace 或发起执行。模板不接收真实模型、endpoint、凭据、Workspace 或私有 Artifact ID；受控枚举只表达作者预定义的逻辑引用，物理资源继续在安装页独立绑定。首版没有远程模板、模板市场或拖拽编辑器。

## 目录、安装与执行 API

以下路径带 `/api/v1`。目录发布和修改由管理员执行，显式安装由当前 Workspace 所有者或管理员执行。

| 操作 | 接口 |
| --- | --- |
| 模板目录与预览 | `GET /workflow-templates`、`POST /workflow-templates/{id}/preview` |
| 发布/校验定义 | `POST /workflows?revision=N`、`POST /workflows/validate` |
| 目录与明确修订 | `GET /workflows`、`GET /workflows/{id}?revision=N` |
| 导出定义 | `GET /workflows/{id}/definition?revision=N` |
| 预览资源 | `POST /workflows/{id}/preview`，body `{workflow_revision,bindings}` |
| 安装资源 | `POST /workflows/{id}/install`，增加 `expected_revision` |
| 读当前安装 | `GET /workflows/{id}/installation` |
| 发起/列出执行 | `POST/GET /workflows/{id}/runs` |
| 读执行与交付 | `GET /runs/{id}` |

CLI 登记脚本不再接受应用或网关密钥参数，`--install` 必须提供 `--bindings`。安装修订与目录修订分别计数；修改模型或工具绑定会形成新的安装范围并保留旧运行。工具策略可使用 workspace_ids/workflow_ids 限制范围，旧 application_ids 仅用于历史范围，不自动解除。兼容执行 API 不进入公开 OpenAPI，已有签名范围仍对普通会话关闭。

## 存储与边界

workflows 保存头修订；workflow_versions 保存不可覆盖的历史定义；workflow_installations 保存各 Workspace 的固定工作流版本和安装修订。旧应用表/绑定继续作为私有存储范围和历史兼容结构保留。业务写入、修订检查和事件同事务提交，外键保证引用的工作流版本存在。

首次初始化统一创建完整当前 Schema，登记 0001_initial_schema 和当前全部命名迁移；已有实例经持锁、先备份的命名迁移升级。目录、安装与执行在同一数据库事务边界内维护。

当前仍为单主进程，节点能力侧重模型协作与文件交付。通用人工资格解析、完整异常分支语言和远程执行尚未实现；外部 Agent 适配不纳入当前路线。定义复用不意味着共享账号、运行权限、凭据或余额。
