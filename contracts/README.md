# 公共契约

Orchpact 的公开契约覆盖工作流声明、应用引用、空间安装与执行。CrewAI 的 Crew、Task、Agent 是每次运行创建的内部执行对象，不属于跨应用交换格式或承诺兼容的 Python 扩展 API。

## 工作流、资源与版本

可复用工作流使用 `format: orchpact-workflow`，声明团队与岗位、模型/工具能力槽位、输入输出、阶段依赖、审校与有界返工。结构字段拒绝实例身份、实际模型标识、endpoint 和凭据；业务 Schema 与提示词按原文保存，分享前由作者审阅。

| 契约 | 校验与说明 |
| --- | --- |
| 可移植定义 | `orchpact workflow-check` 离线校验；完整行为见 [工作流协议](../docs/WORKFLOWS.md) |
| 团队与岗位 | `units[].crew`、节点 `participants`；声明到运行对象的映射见 [团队设计](../docs/TEAM_EXECUTION.md) |
| 资源安装 | 模型绑定选择本地 profile；工具绑定使用 `endpoint_ref + implementation_version` 选择可信 provider。槽位必须恰好覆盖，物理配置不能混入定义 |
| 应用固定引用 | [application-binding.schema.json](application-binding.schema.json)，明确 workflow_id、workflow_revision 和 expected_revision |
| 运行与交付 | [应用 API](../docs/APPLICATIONS.md)，包含权限、幂等、状态、文件与执行步骤元数据 |

工作流作者 `version`、目录修订、应用绑定修订和 Workspace 安装修订分别管理。应用不使用隐式 latest；发布、改绑、安装和执行不自动串联。后续更新不改写已发布定义或已经保存的执行快照。

作者可用 `orchpact-component-workflow` 宿主与固定版本/摘要的本地 `orchpact-component` 文件，通过 `orchpact component-expand` 在发布前展开。参数、JSON/Artifact 端口、命名空间和引用在编译期解析；最终仍是上述普通 `orchpact-workflow`，独立报告不进入定义或运行快照。该作者格式不是 Runtime 插件协议，不引入组件 Registry、远程加载或自动安装。

固定工作流模板由代码内版本和 SHA-256 标识。管理员可读取表单 Schema，并以 `sha256 + parameters` 获取只读规范定义预览；默认值和显式等价参数产生相同定义摘要。预览不创建工作流、应用绑定、Workspace 安装或执行，也不接受真实模型、endpoint、凭据或私有 Artifact ID。发布与安装仍使用各自既有接口。

通用工具 operation 可显式声明 `tool_limit`（0–64）；旧定义不回填该字段，缺省项目不允许通用工具调用。岗位动态调用还须显式声明 `capabilities:["tool:<槽位>"]`，该授权不参与模型能力匹配。显式 `kind=tool` 节点由自身 `tool_ref` 授权，不创建 CrewAI 对象；参数使用固定 `$ref` 输入/Artifact 映射。JSON 结果使用 `files`，二进制结果使用独立 `artifact_outputs` 和 `artifacts` 条目（`data_base64 + sha256 + media_type`）。operation 的 `output_kind=artifact` 使用独立 `artifact_output` 根媒体契约，completed 时 `result=null`，文件仍经授权下载。

Runtime 使用独立 tool_call_id 和 project + logical tool + idempotency_key 幂等边界，run 的 `tool_invocations` 仅暴露状态、引用与摘要。UNKNOWN 不自动重试；显式对账只调用只读 lookup，HTTP 通用 adapter 不支持时需人工核对。只有声明 `retry_policy=safe_only` 的只读节点可在明确失败后由人工显式重试。

`verification` 声明只读 Verifier 和全部阻断性 required_checks。管理员 `tool_verifier` 元数据决定可信身份、检查集合、媒体范围与环境，作者不能自证可信。候选 Artifact 的 PASS 证据绑定 ID/SHA-256/generation/contract_digest 和 Verifier 版本后才允许现有 `review()` 接受；FAIL/INCONCLUSIVE、缺失或过期证据均阻断。run 的 `verification_evidence` 与授权证据读取不包含其他 Workspace/应用数据，waive/partial 不表示完整接受。

确定性 FAIL 可通过 checks 的 node_key/artifact_path/field_path/check_id/repair 进入共享有界返工，选择顺序为有效上游、repair_from、当前节点；只失效受影响分支和必要上级汇总。INCONCLUSIVE/UNKNOWN 不自动返工，execution_plan.repairs 继续受 max_repairs 限制。

团队示例可直接校验，无需实例、数据库或模型：

```sh
uv run orchpact workflow-check examples/workflows/department-review.json
```

## 命令、权限与错误

机器结构：[command.schema.json](command.schema.json)、[mandate.schema.json](mandate.schema.json)、[governance.schema.json](governance.schema.json)。运行实例的完整 OpenAPI 由管理员访问 `/api/v1/openapi.json`；交互文档为 `/api/docs`，不会因此向应用签名身份开放管理权限。

错误为 HTTP 状态码与 `{detail,code?}`。401 身份失效，403 动作或能力未授权，404 资源不存在或不可见，409 幂等/版本/状态/契约冲突，422 请求结构非法，413 请求体超限。授权拒绝不发送供应商请求。

`Command` 统一承载 plan/amend/decide/publish/review/retry/cancel/reply；HTTP 用户入口仅允许用户可执行的 amend/review/retry/cancel/reply，执行尝试的模型决策与发布由内部服务提交，不能通过匿名 Worker 接口伪造。

计划修改中 `changes[].task_id` 是实际工作项 ID；新增 `add_nodes` 使用运行时 PlanNode（unit_id，可省略使用父组织），依赖和父节点为项目内 node key。组织包导入时的 `unit` 稳定别名在安装阶段解析，两种边界不可混用。

执行状态、幂等范围、结果与文件见 [应用接入](../docs/APPLICATIONS.md)；权限和快照语义见 [架构](../docs/ARCHITECTURE.md)。

## 组织包与实现入口

按应用登记的内联组织包由 `orchpact.kernel.packages.validate_package` 进行语义校验，示例见 [evidence-team.json](../examples/evidence-team.json)。包作者 `version` 与服务端并发控制 `revision` 分开处理；多应用共享定义应使用独立工作流资产和应用绑定。

`orchpact.kernel.workflows` 实现纯定义校验、指纹、能力绑定与白名单导出。此处列出源码定位，不将内部模块承诺为稳定库接口。公开兼容边界见 [接口与版本策略](../docs/COMPATIBILITY.md)。
