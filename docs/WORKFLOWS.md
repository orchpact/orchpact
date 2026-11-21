# 工作流资产、应用绑定、空间安装与执行

Orchpact 是可自托管的声明式工作流执行服务。定义描述团队、岗位、阶段依赖、资源需求、交付和验收，CrewAI 执行团队内部协作。定义不依附某个消费应用；多个使用方可以引用同一资产与修订，同时保留各自的接入身份和执行边界。

## 四个独立动作

| 动作 | 标识与版本 | 作用 |
| --- | --- | --- |
| 发布定义 | workflow_id + workflow revision | 独立目录；保存作者 version、SHA-256 与不可覆盖的 JSON 定义 |
| 应用绑定 | application_id + application binding revision | 应用以独立签名身份固定引用 workflow_id + workflow_revision；不使用隐式 latest |
| 空间安装 | Workspace + application_id + installed_revision | 解析职责别名和本地模型绑定，生成该空间/应用独立的组织实例 |
| 发起执行 | run ID | 固定本次输入、原付款人、工作流来源、应用绑定修订、模型与任务快照 |

发布不会修改任何应用；应用更新引用不会升级已安装空间；显式空间升级不重写旧执行。工作流修订与应用绑定修订分别计数，不能混用。

例如 analysis-app 和 report-app 都绑定 department-review revision 1：定义 ID 和指纹相同，应用 ID、公钥、资源绑定、运行、文件和账务边界分别保留。即使在同一个 Workspace 中，A 的应用身份也不能读取 B 的执行、文件或调用回执。

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

确定性 FAIL 可使用 checks 中的 `node_key`、`artifact_path`、`field_path`、`check_id` 和 `repair` 进入现有定向返工：平台按有效上游 node_key、repair_from、当前节点的顺序选择，并沿依赖和必要上级汇总失效，独立 sibling 不变。INCONCLUSIVE、UNKNOWN 和工具执行失败不自动返工。`execution_plans.repairs/max_repairs` 继续限制总次数，旧 Artifact/证据不覆盖，新代次必须重新取得 PASS。

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

## 目录与应用 API

以下路径均带 /api/v1。目录和全局应用绑定仅管理员访问，不加入产品域名或网关白名单。

| 操作 | 接口 |
| --- | --- |
| 固定模板目录 | GET /workflow-templates，管理员，只读，返回版本、表单 Schema 与模板 SHA-256 |
| 模板编译预览 | POST /workflow-templates/{template_id}/preview，管理员，body 为 {sha256,parameters}；不写目录、绑定或安装 |
| 校验提交内容 | POST /workflows/validate，已认证上下文，只读 |
| 发布修订 | POST /workflows?revision=N，body 为定义；N 是当前目录头修订，新建为 0 |
| 列目录 | GET /workflows |
| 读特定修订 | GET /workflows/{workflow_id}?revision=N；省略仅用于读取当前头 |
| 导出特定修订 | GET /workflows/{workflow_id}/definition?revision=N |
| 创建/更新应用绑定 | POST /apps/{application_id}/binding |
| 读取应用与固定来源 | GET /apps/{application_id}，沿用应用签名和成员检查 |
| 导出应用引用的定义 | GET /apps/{application_id}/definition |
| 预览空间安装 | POST /apps/{application_id}/preview，body 为 {bindings:{models:{...}}} |
| 确认安装 | POST /apps/{application_id}/install?expected_revision=N&binding_revision=M，同上 body |
| 发起执行 | POST /apps/{application_id}/runs，沿用原有幂等、授权、余额与付款人规则 |

应用绑定示例：

```json
{"name":"研究应用","workflow_id":"shared-workflow","workflow_revision":1,"expected_revision":0}
```

workflow_revision 必须明确且存在。expected_revision 是当前应用绑定修订，新应用为 0。可由管理员显式传 enabled/require_signature，省略时新建默认均为 true，更新时保留原值。应用标识不需要与工作流标识相同。

应用读模型的 revision 是应用绑定修订，workflow.revision 是固定工作流修订，installed_revision 是当前空间已安装的应用修订。manifest 返回所引用的作者定义，其 id 仍是工作流 ID。

安装时 expected_revision 使用当前空间安装修订（新建 0），binding_revision 使用预览返回的应用 revision。任一变化会拒绝旧预览。显式绑定/升级仍需空间所有者或管理员。相同应用修订不能暗中改绑模型或工具；先生成新的应用绑定修订，再显式升级空间。

run.workflow 保存 id、version、revision、sha256、application_binding_revision 与本次模型/工具 bindings；声明工具时另有安全 `tool_bindings` 摘要。不同应用可有不同绑定修订和资源映射，但同一工作流修订的定义指纹保持相同。

## SDK 与内联组织包

orchpact.interfaces.client.configure 支持 --application、--application-name、--install 和 --bindings。公开包装入口为 examples/register-workflow.py。绑定文件只发送安装接口，不混入工作流定义。管理凭据只从环境读取，私钥由应用独立保管。

--update 明确发布工作流新修订并更新指定应用引用；其他应用继续使用自己的固定引用。空间安装还需 --install。没有 --application 时，configure 通过 organization-packages 登记与应用同标识的内联组织包。需要多个应用复用时，应发布独立工作流资产。

organization-packages 保留按应用登记的内联组织包。已引用独立工作流的应用拒绝通过该入口覆盖，必须显式更新 binding。内联组织包与目录引用是两种明确的登记方式；应用身份与执行权限保持各自边界。

## 存储与边界

workflows 保存头修订；workflow_versions 保存不可覆盖的历史定义；application_bindings 保存每次应用绑定。业务写入、修订检查和事件同事务提交，外键保证引用的工作流版本存在。

首次初始化统一创建完整当前 Schema，登记 0001_initial_schema 和当前全部命名迁移；已有实例经持锁、先备份的命名迁移升级。目录、绑定与执行在同一数据库事务边界内维护。

当前仍为单主进程，节点能力侧重模型协作与文件交付。通用人工资格解析、完整异常分支语言和远程执行尚未实现；外部 Agent 适配不纳入当前路线。定义复用不意味着共享账号、运行权限、凭据或余额。
