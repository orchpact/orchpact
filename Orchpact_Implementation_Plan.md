# Orchpact 下一阶段工程实施规范（AI 编程 Agent 交付版）

> 状态：以当前仓库源码、测试和文档为核对基线。**阶段 0–8 已完成：工具声明与绑定、统一 Tool Runtime、Agent Tool Call、确定性工具节点、Artifact 导入与二进制根输出、可信 Verifier/Evidence/Acceptance、复用现有依赖图的定向返工、JSON 工作流组件编译期展开，以及固定模板与原生参数表单已经实现；阶段 9 仍为拟实施设计。** 第 1 节区分现有能力与尚未实现的能力。实施前须重新核对目标分支，禁止把后续阶段的示意格式机械复制为公共契约。
>
> 目标：**可复用的工作流 + 可扩展的外部工具 + 可验证的交付质量**。保留 JSON，不引入 CUE。

## 0. AI 执行者必须遵守的工程约束

1. 阅读 `AGENTS.md`、`CONTRIBUTING.md`、`docs/ARCHITECTURE.md`、`docs/WORKFLOWS.md`、`docs/EXECUTION_BOUNDARY.md`、`docs/TEAM_EXECUTION.md`、`docs/RELEASE_VALIDATION.md` 和目标模块测试；先写现状与变更设计，再写代码。
2. 只能增量扩展已有 Orchpact。**不重写 CrewAI 的团队任务与单岗位行动循环、平台状态机、权限、预算、账务、不可变产物或工作流发布/安装机制**。保留 `workflow_id + revision`、application binding revision、Workspace installed revision、run 固定来源以及 work item/attempt 快照的分离。团队声明长期复用，但每次实际启动仍须创建新的 Crew、Task、Agent。
3. 保持已有 JSON 工作流合法且行为不变。新增字段必须通过明确版本兼容策略；严格未知字段校验不能直接删除。现有 `validate_definition()` 和 `validate_package()` 仍是发布入口的权威校验，`bind_definition()`、`materialize()`、`export_definition()` 及安装预览也必须同步处理新增资源。
4. 保持应用身份与 Workspace 隔离、原付款人、幂等、回执恢复、文件限制。不得自动重放未知结果或未知付费请求。不得把租户凭据写进 Workflow Definition、Agent Prompt、日志、Artifact、快照明文或导出包。
5. 使用现有 PostgreSQL 与配套文件目录；单主进程边界不变。任何数据库 Schema 变更须同时处理 `adapters/schema.py` 新实例初始化、持锁命名迁移、升级前 `pg_dump`、数据库与文件成对备份恢复、回滚和已有数据兼容。
6. 单元/集成测试不调用付费模型、不执行不可信生成代码、不连接生产数据库、不依赖外部网络。外部工具使用可控 mock HTTP/MCP server。使用仓库约定 `uv sync --frozen`、`uv run python -m unittest discover -s tests -p 'test_*.py'`、`node --test tests/*.test.cjs`、`git diff --check`。PostgreSQL 使用 `ORCHPACT_PG_BIN` 或隔离的 `ORCHPACT_TEST_DATABASE_URL` / `ORCHPACT_PG_DUMP`；无测试数据库时明确标注未运行，不伪造通过。
7. 每个阶段独立提交（Conventional Commits），提交前说明变更、兼容性影响、测试与未解决风险。**不自行部署、发布、升级 Workspace 安装或调用真实付费服务**。
8. 在实现前先查找仓库内同等能力；已存在则复用或改造，不增加平行状态机/重复 Registry/重复验收系统。
9. 公开行为变化须同步更新 `docs/COMPATIBILITY.md`、相关工作流/API 文档与 `contracts/`；前端沿用原生 HTML/CSS/JavaScript，不引入前端包管理器。消费者文档业务规则留在独立参考应用，不进入 Core。

## 1. 现有源码事实（核对基线）

- `src/orchpact/kernel/workflows.py`、`src/orchpact/kernel/tools.py`：`resources` 支持严格校验的 `models` 与 `tools`。工具声明包含有界接口/版本、Schema、效果、限额、权限和 Artifact 契约；旧定义不回填 `tools:{}`。`bind_definition()` 要求模型和工具绑定恰好覆盖槽位，`definition_digest()` 计算规范化 JSON 的 SHA-256，导出只保留逻辑工具定义。
- `src/orchpact/kernel/plan.py`、`src/orchpact/kernel/models.py`：`PlanNode` 支持显式 `kind=agent|tool|group`、`tool_ref` 和有界 `arguments`。省略 `kind` 时仍以 `staff`/`participants` 判断 Agent 与组织汇总；工具节点是执行节点，不能复用无角色的汇总语义。`outputs` 继续保持 JSON 文件到 JSON Schema 的映射。
- `src/orchpact/adapters/crewai.py`：`ScopedTool`、`PlatformLLM`、`prepare_role()` 和 `run_crew()` 保留 CrewAI 行动循环。`run_crew()` 按冻结 roster 创建顺序 Crew/Tasks/Agents；`prepare_role()` 注入原有 `read_artifact`、`search_evidence`，并按岗位自身 `tool:<槽位>` 授权注入通用 Runtime 工具。动态参数模型复用原 JSON Schema；搜索观察保留专门的有界 JSON 处理，通用观察包含 tool_call_id 且有 24,000 字符上限。副作用不在 `PlatformLLM.call()` 中执行。
- `src/orchpact/services/agent_steps.py`、`src/orchpact/adapters/providers.py`：每个 attempt 默认共享 12 个模型步骤、8 个工具步骤、300 秒和 128000 字符历史。模型、搜索和图片请求已有授权、预算、预留、真实回执与恢复机制；`agent_steps` 是执行证据索引，不是通用外部工具的幂等调用账本。
- `src/orchpact/services/validation.py`：`DeliveryContracts.check()` 已支持 JSON 交付文件、Schema 以及 `unique`、`references`、`sum_equals_input`、`same_length`、`nonempty_references`、`current_inputs`、`source_urls`、`equals_dependency`、`text_references`、`scoped_references` 等确定性规则；工具节点复用同一检查，不另建交付规则。
- `src/orchpact/services/platform.py`：`publish()` 先检查契约和治理输出限制，再发布 Artifact；`review()` 校验当前 artifact_id、可选 digest 和独立审校政策，并记录 artifact SHA-256 与 generation；`reconcile()` 处理无模型组织汇总、gate 报告及按 `issues[].node_key`、`repair_from` 定向返工；`invalidate()` 向下游及受影响的上级汇总节点传播失效，增加 generation 并清除任务当前产物引用，历史 Artifact 行与文件仍保留。纯工具安装与根工作项不再强制选模，工具工作项只冻结本槽位授权。
- `src/orchpact/services/executor.py`：文本工作经 CrewAI 执行，已有 JSON 协议与应用契约的有界修正；图片工作直接走平台图片提供者；`ToolNodeExecutor` 在 CrewAI 路径前分派确定性工具节点，解析安全引用、调用 Runtime 并走现有交付验收，不创建 CrewAI 对象或模型请求。
- `src/orchpact/kernel/files.py`、`src/orchpact/adapters/artifacts.py`、`src/orchpact/services/tool_artifacts.py`：**文件型 Artifact 与受控导入已存在**。支持文本和二进制字节、单文件与多文件 ZIP、manifest/SHA-256、安全相对路径、单次最多 20 个文件和 10MB、每项目 500MB，以及读取时摘要校验；工具 base64 条目还需通过媒体与 ZIP/OOXML 检查。二进制内容不会内联进 Agent 摘录。`kernel/verification.py` 和 `output_kind=artifact` 提供独立媒体及根输出契约。
- `src/orchpact/adapters/configuration.py`：管理员可登记 `category=tool` 的 HTTP/MCP/builtin provider，声明接口、实现版本、Workspace/应用范围、网络与并发策略。工具使用只写托管秘密，安装时派生不含 URL/秘密引用的安全绑定摘要。MCP 配置可以兼容读取，但传输与 SDK 未评审前拒绝绑定。
- `src/orchpact/services/workflows.py`、`src/orchpact/services/platform.py`：目录发布、应用固定修订、Workspace 预览/安装相互独立；已安装 manifest 保存原始模型/工具选择和安全工具摘要，`invocations.binding` 冻结内部工具运行时记录，工作项 `tasks.snapshot.tools` 冻结实际 provider 与凭据引用。同一应用修订不能暗中改绑资源，语义配置变化要求显式升级，旧 run 和明确重试不随配置/凭据轮换改变。
- `src/orchpact/services/tools.py`、`src/orchpact/adapters/tools.py`、`src/orchpact/adapters/schema.py`：统一 Tool Runtime、代码内静态 HTTP/builtin Registry 和 `tool_invocations` 已实现。调用固定 attempt/主体/槽位授权、参数与响应 Schema、依赖 Artifact 范围、项目/attempt/provider 限额、原付款人预留结算、私有回执和恢复；幂等范围是 project + logical tool + key。`invoke_node()` 复用同一调用流程实现节点幂等回读和有限安全只读重试。HTTPS 固定已校验 IP 并保留 TLS 原域名，UNKNOWN 外部写只能只读 lookup 或人工对账，不自动重放。
- `src/orchpact/kernel/components.py`、`src/orchpact/cli.py`：阶段 7 已实现固定版本与 canonical SHA-256 的本地 JSON 组件展开。参数、输入/输出端口、嵌套、命名空间、工具/Verifier/Artifact 契约和引用在编译期重写；组件与最终宿主复用 `validate_definition()`。`component-expand` 离线生成普通工作流及独立 source map 报告，不加载 Settings，不覆盖已有文件。
- `src/orchpact/kernel/templates.py`、`src/orchpact/static/kernel/template-form.js`：阶段 8 已实现固定版本/摘要模板、服务端默认值与 JSON Schema 校验、普通工作流预览和原生参数表单。管理员只读接口不发布或安装；表单支持嵌套对象、标量、枚举和标量数组，参数变化废弃旧预览，显式登记仍进入原发布编辑器。
- `evals/README.md`：scripted 测试明确标记 `synthetic=true`、`quality_evidence=false`，不能拿合成通过率冒充真实模型质量、真实费用或注入防御证据。

### 1.1 尚未实现的通用能力

当前 Python 源码中仍没有 MCP transport 或独立文档交付参考工具链。Tool Runtime 已接入 CrewAI 动态调用、确定性 JSON/Artifact 工具节点和可信 Verifier；受控 Artifact 导入、二进制根输出、不可变 Evidence/Acceptance、有界定向返工、编译期 JSON 组件展开与固定模板参数表单已实现。独立参考应用不能被宣称为上述后续能力已经完成。

## 2. 目标范围与边界

**必须交付**：工具定义/绑定、受控 Tool Runtime、Agent Tool Call、Deterministic Tool Step、外部验证器/证据/验收绑定、**现有文件 Artifact 的工具导入与媒体契约扩展**、已有失效/返工能力增强、JSON 组件展开器、模板参数表单基础、独立参考文档工具链与端到端测试。

**明确不做**：CUE/YAML 作为新运行时语言、独立组件运行时、组件市场、通用插件商店、远程 Worker/分布式调度、模型字节级确定性、通用 Python 任意代码工具、无限制本地 shell、复杂拖拽编辑器、在 Core 中硬编码 Word/Excel/PDF/企业数据源业务逻辑、让 LLM Judge 独立决定验收、运行时组件热替换、跨工作流共享组件执行状态。

## 3. 执行顺序与依赖图

```
[0 基线/兼容测试]
       |
       +--> [1 Tool Definition + Binding + Snapshot]
       |         |
       |         +--> [2 Tool Runtime + Audit + Recovery]
       |                   +--> [3 Agent Tool Call]
       |                   +--> [4 Deterministic Tool Step]
       |                               |
       |                               +--> [5 Artifact Import + Verifier + Evidence + Acceptance]
       |                                              |
       |                                              +--> [6 Repair / Invalidation]
       |
       +--> [7 Component Expander] (需复用第1阶段工具声明格式；可并行)
                      |
                      +--> [8 Template/Form] (还需工具与契约 Schema)

[3,4,5,6,7] --> [9 Reference Application + E2E + Evaluation]
```

阶段编号仅代表推荐依赖顺序，不代表商业优先级。第 5 阶段内部可以并行设计 Artifact 导入与 Verifier，但必须先确定不可变产物引用及证据绑定协议。

## 4. 阶段 0：基线冻结与设计决策

**输入**：当前源码、文档、测试。

**任务**：
- 输出 `docs/design/tool-component-verification.md`：按代码列出现有调用链 `WorkflowCatalog.register()/bind() → Platform.prepare_package()/preview_package()/install_package() → Platform.invoke()/install_plan()/new_task() → claim/context → CrewAIExecutor → complete_work/publish → review/reconcile`，并标明 `validate_definition()`、`bind_definition()`、`materialize()` 所在位置；逐处列出将修改的文件和原因。
- 对比现有 `resources.models`、已安装 manifest 的 `_workflow.bindings`、`invocations.binding` 与 `tasks.snapshot`，明确 `resources.tools` 的生命周期：**逻辑接口属于 Workflow Definition；物理服务、凭据引用属于 Workspace/应用绑定；run 固定来源与绑定，工作项固定实际执行配置；实际凭据只在受控环境解析，不进入快照正文**。
- 明确新增节点采用 `execution_plan.nodes[].kind=tool` 还是互斥 `tool` 字段；现有节点不声明时保持旧语义。必须同时检查 `kernel/models.py`、`kernel/workflows.py`、`kernel/plan.py`、`kernel/packages.py`、`services/platform.py`、`services/executor.py`、`services/validation.py` 以及计划变更/导出路径，尤其不能把无 `staff`/`participants` 的组织汇总节点误判为工具节点。
- 明确根产物协议：当前 `kernel/packages.py` 要求 operation `output_file` 为 JSON，`reconcile()` 对根产物执行结构化 `result()` 解析。若工具工作流要直接交付 DOCX/PDF/ZIP，必须单独设计 run 结果与下载响应的兼容策略，不能只放开节点文件扩展名。
- 明确工具预算归属：复用现有 payer、`requests`、credit policy、预留/结算与 usage 机制；如外部工具成本不可量化，明确调用限额和未知费用状态，不得静默记零、把内部积分当供应商金额或越权扣费。
- 复用并补充现有兼容性测试夹具，至少覆盖 `test_workflows.py`、`test_workflow_catalog.py`、`test_team_crews.py`、`test_crewai_execution.py`、`test_planned.py`、`test_kernel.py`、`test_runtime.py` 中的应用/Workspace 隔离、固定快照、团队交接、返工、账务、文件摘要与未知结果恢复。

**验收**：有可审查的设计决策文档与基线测试；明确未解决的 Schema/迁移问题。未完成不得大规模改写核心。

## 5. 阶段 1：Tool Definition / Binding / Snapshot（已完成）

### 5.1 Tool Definition

`src/orchpact/kernel/tools.py` 已实现严格工具声明：`interface`、`version`、`description`、`input_schema`、`output_schema`、`effect`（`read` / `create_artifact` / `external_write`）、`timeout_ms`、`max_output_bytes`、`retry_policy`、`idempotency`、`required_permissions`、`artifact_contract`。Schema 有大小、深度和本地引用限制；`create_artifact` 必须声明 Artifact 契约。

当前合法定义片段：

```json
{
  "resources": {
    "models": {},
    "tools": {
      "document_render": {
        "interface": "document.render",
        "version": "1",
        "input_schema": {"type":"object","required":["template_id","data_artifact_id"]},
        "output_schema": {"type":"object","required":["artifact"]},
        "effect": "create_artifact",
        "artifact_contract": {
          "media_types": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
          "max_files": 1,
          "max_bytes": 10485760
        }
      }
    }
  }
}
```

接口版本与部署实现版本分开，不接受动态 `latest`；结构化工具字段禁止 URL、密码、token 和连接串。工具权限不混入模型 `capabilities`。提示词和业务 Schema 不做任意文本秘密扫描，导出前仍须作者审阅。

### 5.2 Tool Binding

安装输入只包含 `endpoint_ref + implementation_version`，选择 `ProviderConfiguration` 中管理员登记的 `category=tool` provider。provider 声明 `http|mcp|builtin` adapter、接口/版本和 Workspace/应用策略；真实 URL 与只写托管秘密不进入定义或安装输入。

安装预览/确认沿用 revision 冲突检查。`_workflow.bindings.tools` 保存原始选择，`_workflow.tool_bindings` 保存 adapter 类型、接口/实现版本、endpoint_ref 和配置摘要，不含 URL、provider 记录或 `_secret_ref`。同一应用修订不能改绑；更新需要新应用修订和 Workspace 显式升级。

### 5.3 执行快照

新 run 核对当前 provider 与安装安全摘要，把逻辑定义、安全绑定和冻结 provider 记录保存到内部 `invocations.binding.tools`；工作项 `tasks.snapshot.tools` 复制该记录。公开 run 只返回 `_workflow` 安全元数据。

非秘密语义配置变化后，旧安装不能创建新 run；旧 run 和明确重试继续使用原快照。仅托管凭据轮换时，新 run 使用新引用，旧 run/重试使用旧引用。阶段 1 出口时只保存声明与快照；实际 Tool Runtime 和费用由阶段 2 增加。

**验收结果**：旧 workflow 摘要与行为不变；缺失/额外槽位、版本不兼容、跨 Workspace/应用绑定和结构化秘密均被拒绝；预览只读、导出清理、公开响应无秘密、配置升级与凭据轮换语义均由 `tests/test_tools.py` 覆盖。

## 6. 阶段 2：Tool Registry / Runtime / Audit（已完成）

### 6.1 统一接口

`services/tools.py::ToolRuntime` 提供 `invoke()/recover()/lookup()`；`adapters/tools.py` 的静态 Registry 只启用 HTTP 和代码内 builtin。`AdapterContext` 携带冻结定义、安全绑定、provider、参数、调用/幂等 ID 和仅 adapter 可见的凭据。lookup 是可选只读能力，通用 HTTP 不支持，MCP 尚未开放。

### 6.2 已实现的固定调用流程

1. 检查停止标志、当前 attempt、墙钟期限、Workspace/应用、`tool:<slot>` 与 `required_permissions` 授权。
2. 校验工作项冻结快照、配置摘要、provider 状态、参数大小/Schema 和依赖 Artifact 白名单。
3. 以 project + logical tool + idempotency key 查重；已完成返回原私有结果，其他已有状态拒绝重放，摘要不一致拒绝冲突。
4. 在同一事务检查项目 `tool_limit`、attempt `tool_steps` 和 provider 并发，创建 `tool_invocations` 意图、requests 预留和原付款人钱包 held。
5. adapter 只执行一次；HTTPS 应用固定 IP、TLS 原域名、deadline、响应字节和重定向策略。
6. 校验响应 Schema、大小和 Artifact 引用；私有回执先落盘，再原子更新调用、requests、钱包与事件。
7. external_write 的未知结果保持 unknown/held，只能只读 lookup 或人工对账。Runner 恢复、重复请求和明确 retry 均不重放 UNKNOWN。

### 6.3 安全边界

- HTTP 逐跳检查 host/port 和全部 DNS 地址，socket 固定已验证 IP，TLS 保留原 SNI/hostname；默认拒绝非公网地址，私有地址须显式允许。
- 禁止环境代理；跨主机重定向不带 Authorization，external_write 不跟随重定向。响应有界分块读取，系统 DNS 解析器本身的超时仍依赖部署环境。
- MCP SDK/transport 未引入；配置可兼容读取但绑定拒绝。无动态 Python 插件、任意 shell 或生成代码执行。
- 阶段 2 只校验已有依赖 Artifact 引用；阶段 5 已增加受控文件导入与媒体契约。工具输出仍是数据，不能提升指令权限。

### 6.4 审计与数据结构

`tool_invocations` 已由 `0002_tool_runtime` 命名迁移及新实例 DDL 创建，记录 run/task/attempt、Workspace/应用、逻辑槽位、接口/实现、binding/args/result 摘要、request_id、状态、恢复方式和必要索引；`projects.tool_limit` 历史缺省 0。参数、结果与回执位于私有 `var/tool-invocations/`，事件和 run 查询不含正文或秘密。`agent_steps` 尚未与通用调用关联，阶段 3 必须复用同一 tool_call_id 而非重复执行。

**验收结果**：`tests/test_tool_runtime.py` 覆盖授权、参数/响应、额度与并发、免费/未知价格、幂等与跨 attempt、UNKNOWN 零重放、回执前后崩溃、伪造回执、显式 lookup、DNS/TLS 固定、重定向、输出限制、命名迁移和数据库/文件成对恢复。真实公网、MCP、CrewAI 注入和工具节点不在本阶段验证范围。

## 7. 阶段 3：Agent Tool Call（已完成）

- `adapters/crewai.py::prepare_role()` 已按冻结工具快照和岗位自身 `capabilities` 中的 `tool:<槽位>` 授权动态注入 `ScopedTool`。模型只选择工具，CrewAI 调度 `BaseTool`，`PlatformLLM.call()` 不直接执行副作用。工具授权在选模和导出模型需求时过滤，不混入 `resources.models[].capabilities`。
- `read_artifact` / `search_evidence` 保持原专用路径、工具名、依赖产物白名单、同项目检查、岗位 `search` 能力、来源额度、真实请求回执和费用语义，不重复进入通用 Runtime。规划和接收阶段 `allow_tools=False` 不变。
- 动态参数模型复用工具原 JSON Schema 做 CrewAI 入口校验；Tool Runtime 再授权并校验当前 attempt、Workspace/应用策略、参数、Artifact 引用和响应。每个岗位只获得自己的工具，同 Crew 授权不共享。
- Agent 工具幂等键使用工作项、槽位和规范化参数的稳定摘要；同参数跨 attempt 会命中原记录，completed 返回私有原结果，started/failed/unknown 阻断重放。新行动与首次调用共用 ID，重放或 UNKNOWN 阻断的私有行动证据记录原 tool_call_id。
- 通用观察包含 tool_call_id 并有 24,000 字符上限；输出只作为数据进入历史，系统指令保持不变。搜索继续使用保留完整 JSON 和来源身份的专用截断逻辑。UNKNOWN 通过 `ExecutionAborted` 中止，不交给 CrewAI 自动重试。

**验收结果**：`tests/test_tool_runtime.py` 新增真实 CrewAI 动态调用、非法参数零副作用、岗位不继承同 Crew 授权、恶意/超长观察、UNKNOWN 跨恢复与明确重试零重放及 Crew/岗位/attempt 审计关联；`tests/test_crewai_execution.py` 确认搜索不产生通用工具请求或费用，`tests/test_tools.py` 覆盖工具授权与模型需求分离。现有读取、搜索和团队回归保持通过。

## 8. 阶段 4：Deterministic Tool Step（已完成）

- `execution_plan.nodes[]` 已支持显式 `kind=agent|tool|group`。旧节点不声明类型时，`staff`/`participants` 仍走现有 CrewAI 路径，无执行角色的组织节点仍走确定性汇总；工具节点不创建 CrewAI 对象，也不占用组织节点的汇总语义。
- 工具节点声明 `tool_ref`、有界 `arguments`、直接 `depends_on` 和 JSON `outputs`。参数支持字面 JSON 及固定 `$ref=input/artifact/json` 引用；发布/安装检查依赖、文件与可静态判断的类型，运行时重新核对当前已验收 Artifact 和完整输入 Schema。没有表达式、Python、环境变量或任意字符串插值。
- `outputs` 继续表示 JSON Schema。工具结果统一为 `{"files":{...}}`，并复用 `complete_work()/publish()` 的精确文件、Schema、rules、治理和不可变 Artifact 检查，再由 `review()/reconcile()` 验收；工具 completed 不等于 accepted。
- `ToolNodeExecutor` 在 CrewAI 路径前分派，`ToolRuntime.invoke_node()` 复用统一授权、限额、原付款人、账务、私有回执和恢复。节点 completed 跨 attempt 幂等回读；UNKNOWN/started 不重放；只有 `read + safe_only` 的明确 failed 调用允许人工显式重试并保留新记录。
- 工具工作项使用 `executor.kind=tool` 和 deterministic profile/attempt，只冻结本槽位工具与最小授权。无 Agent/团队/动态操作的纯工具定义在预览、安装、根工作项和执行中均不强制文本选模；混合工作流仅 Agent 路径选模。计划新增/重建继续读取 run 冻结的工具定义。

当前合法节点片段（工具自身 `output_schema` 还须声明对应 `files`）：

```json
{"key":"render","kind":"tool","tool_ref":"document_render","depends_on":["facts"],
 "description":"Render accepted facts","expected":"result.json",
 "arguments":{"template_id":"deployment-v3",
              "data_artifact_id":{"$ref":"artifact","node":"facts","file":"project-facts.json"}},
 "outputs":{"result.json":{"type":"object"}}}
```

阶段 4 只开放 `read` 与 `external_write` 的 JSON 节点。`create_artifact` 节点、`artifact_outputs`、`verification`、二进制 `output_kind` 和根二进制 `result` 均未开放，留待阶段 5 与真实文件导入、媒体契约和验证证据一起实现。

**验收结果**：`tests/test_tool_nodes.py` 覆盖旧摘要与三类节点、无可用模型时的安装执行、单/多 JSON 文件、输入/Artifact/组织导出映射、依赖验收阻断、计划变更、交付失败不发布、completed 零重放扣费、UNKNOWN 零重放及 safe_only 的人工重试。没有数据库 Schema 变更或 CrewAI 运行对象。

## 9. 阶段 5：Artifact、Verifier、Evidence 与 Acceptance（已完成）

### 9.1 文件型 Artifact 导入与契约扩展

- `kernel/verification.py` 与 `PlanNode.artifact_outputs` 提供独立二进制路径、媒体类型和大小契约，不改变 `outputs` 的 JSON Schema 含义。`create_artifact` 必须声明产物输出，工具声明的 artifact_contract 限制媒体集合、文件数与总大小。
- `services/tool_artifacts.py` 只接受有界 `data_base64 + sha256 + media_type` 条目，重新解码并核对摘要、大小、路径和实际内容，不接受宿主路径或工具直接写正式目录。支持 JSON/UTF-8、PNG/JPEG/WebP、PDF、ZIP、DOCX/XLSX/PPTX；ZIP 检查链接、穿越、加密项、条目/解压大小和异常压缩比。其他媒体明确拒绝，基础结构检查不冒充真实渲染或业务质量。
- 导入后复用 `DeliveryContracts`、`encode_files()` 和 `Artifacts.publish()` 的不可变 ID、manifest、摘要、1–20 文件、10MB、项目 500MB 和下载授权。base64 传输计入工具 max_output_bytes，不扩大原限额。
- operation 支持显式 `output_kind=artifact + artifact_output`。根汇总仍生成不可变 Artifact，completed 时 `result=null`，使用现有授权下载；根 Artifact 独立执行其验证策略。

### 9.2 可信 Verifier Registry

- 管理员 provider 的 `tool_verifier` 声明 checks、media_types、trust_level 和 environment，并进入配置摘要与冻结快照。只有 `effect=read` 且管理员显式 trusted 的绑定可用于验收；作者不能自证可信。变更仍需新应用修订和 Workspace 显式升级。
- 节点/固定 operation 的 `verification` 声明 `tool_ref + required_checks + parameters`，所有 required_checks 均为阻断项。Verifier 输入的 Artifact ID/SHA/generation/contract_digest 由平台构造，生产者不能替换。
- 统一输出 `PASS|FAIL|INCONCLUSIVE` 与有界 checks。HTTP 200 不是 PASS；未知/缺失/矛盾检查、非可信身份或不支持的媒体均不能通过，总体 PASS 不会掩盖 FAIL 检查。

### 9.3 Evidence Bundle

- `verification_evidence` 记录 run/task/attempt、Artifact ID/SHA-256/generation、contract_digest、Verifier 接口/实现/binding 摘要、tool_call_id、状态、checks 和 bundle_digest；`0003_verification_evidence` 同步覆盖新实例 DDL 与持锁、先备份的命名迁移。
- 私有 bundle 位于 `var/verification-evidence/<attempt>/<tool_call_id>.json`，tool_call_id 兼作证据 ID，重复提交必须一致。run 返回安全元数据，授权 `GET /api/v1/verification-evidence/{id}` 返回元数据与 bundle。
- 数据库、Artifact、工具回执和证据文件纳入同一成对备份恢复边界。更换 Artifact、契约或 generation 后旧证据不可用，最终化与 `review()` 都执行当前对象检查。

### 9.4 Acceptance 与恢复时序

- `publish()` 先发布候选 Artifact，但声明 verification 的 task/attempt 保持 running、review pending；普通下游和人工 accept 均不能绕过。Verifier 在事务外复用 Tool Runtime，证据落库后 PASS 才完成并进入现有 reconcile/review，FAIL/INCONCLUSIVE 失败。没有新增平行 accepted 状态。
- Agent、工具、组织与根汇总共用候选分派；根/组织候选不会重复汇总。独立人审继续在其后适用，waive 仍只产生 partial/waived。
- Runner 恢复先恢复工具回执，再恢复已有证据或 completed 验证回执，最后才把剩余 running 标为 interrupted；没有 completed 调用时不主动发起验证。UNKNOWN 不因换 attempt 或新 Artifact 重放，completed 不重复扣费。

**验收结果**：`tests/test_tool_artifacts.py` 覆盖路径、摘要、大小、MIME、ZIP/OOXML、JSON+二进制与二进制根下载；`tests/test_verification.py` 覆盖 Agent/工具/根 PASS、FAIL/INCONCLUSIVE、伪造结果、非可信绑定、过期/缺失证据、waive、API 隔离、候选各恢复点、UNKNOWN 零重放、命名迁移及成对恢复。旧 JSON 流程保持原行为，未新增人工例外接受。

## 10. 阶段 6：失效传播与定向返工（已完成）

- `Platform.repair()` 集中原 gate 与 Verifier 的返工入口，继续按有效上游 `issues[].node_key` → `repair_from` → 当前节点选择，复用 `invalidate()`、`plan.rework` 和 `execution_plans.repairs/max_repairs`，没有第二套调度或数据库迁移。
- `CheckResult` 增加可选 `node_key` 与 `artifact_path`，保留 `field_path/check_id/repair` 和旧 `path`。只有绑定不可变证据的确定性 FAIL 才进入自动返工；未知定位不猜测上游。
- 仅使目标、下游及必要上级汇总失效。共享事实源变化会使全部依赖者失效，独立 sibling 的 turns、Artifact 与 generation 不变。历史 Artifact 和 FAIL 证据保留，新候选必须取得新 PASS。
- INCONCLUSIVE、UNKNOWN、工具执行失败和缺少证据不进入自动返工；waive 仍只是 partial/waived。达到额度后直接失败，原付款人、工作项 12 次和项目 96 次上限不变。
- 后续执行继续使用原工具快照与幂等回执，completed 结果只读回不重复副作用或费用。

**验收结果**：`tests/test_repair.py` 覆盖单文档定向返工、共享事实传播、未知定位回退、额度耗尽和 INCONCLUSIVE 不返工；独立 sibling 与历史 Artifact/证据保持不变。原 gate 与阶段 5 验证恢复测试继续通过。

## 11. 阶段 7：JSON Workflow Component Expander（已完成）

### 11.1 定位与作者格式

组件是**编译期工作流子模块**，不是运行时对象。`src/orchpact/kernel/components.py` 实现 `orchpact-component-workflow` 宿主和 `orchpact-component` 组件；组件引用、参数绑定、命名空间、嵌套、版本解析在发布前完成，Runtime 只接收现有标准 Workflow JSON。

组件文件名为 `<component_id>@<version>.json`，实例 `use` 必须精确匹配并另附 canonical `sha256`，不允许 latest、URL 或远程加载。组件声明 `parameters_schema`、必填 JSON/Artifact `inputs`、命名 `outputs`、普通 `resources/units/agents/operation` 和可选嵌套 `components`；实例以 `with/inputs/ports` 绑定参数和端口，同一组件可多次实例化。

### 11.2 已实现的展开职责

1. 严格 allowlist、源文件大小、固定版本和摘要校验；检测缺失、循环、超过 4 层或 32 个实例，并继续执行现有节点数、计划和定义大小限制。
2. 参数先通过 JSON Schema，仅替换完整 `{"$param":"name"}` 或带 `path` 的对象；没有字符串模板、环境变量/秘密插值或代码求值。
3. 节点、unit、模型/工具与 Verifier 槽位使用完整 `<instance>__<key>`；重写 `depends_on`、`repair_from`、`result_node`、`$ref`、输出文件、工具输出 Schema、校验规则和组织 `exports`，跨实例资源/unit/Agent 冲突直接拒绝。
4. 组件输入在最终宿主边界解析为现有 `$ref=json/artifact`；输出端口同步重写 JSON/Artifact 文件，宿主契约必须与组件源契约一致。
5. 每个组件展开后及最终宿主均调用 `validate_definition()`；返回规范化 `orchpact-workflow`，不修改发布 API 或安装协议。
6. 独立报告保存 `expander_version`、组件 ID/版本/摘要、实例路径、最终 `definition_digest()` 和 `source_map`。source map 将最终实际节点映射到当前组件原内部 key，嵌套节点只归属其实际组件，不进入定义或运行快照。
7. 相同语义 JSON 的格式化变化不影响最终执行定义摘要；固定版本与摘要保证可复现展开。

### 11.3 离线 CLI

```sh
uv run orchpact component-expand author.json \
  --components-dir ./components --output expanded.json --report expanded.report.json
```

命令不加载 `Settings`，输出与报告必须是不同的新文件。展开后仍需显式发布、应用绑定和 Workspace 安装；组件升级生成新定义修订，已有执行不热更新。

### 11.4 组件契约与验收

组件验收不替代全局验收，多个文档各自通过仍须跨文档一致性验证。没有组件注册中心、独立数据库或运行时热替换；组件源文件不得携带实例模型、endpoint、凭据、上传内容或运行证据。

**验收结果**：`tests/test_components.py` 的 7 项测试覆盖双实例、嵌套/循环、参数/摘要/引用/契约拒绝、Artifact 与 Verifier、组织 exports、格式化摘要稳定性、深度/实例/节点/大小限制、命名冲突和 CLI 离线/禁止覆盖。旧 `workflow-check`、发布、绑定、安装及 Runtime 路径未改变。本阶段全量回归实际通过 Python 339/339（含隔离 PostgreSQL）、Node 20/20、契约验证 7/7、scripted eval 36/36；scripted 结果不代表真实模型质量。

## 12. 阶段 8：Workflow Template + 参数表单（已完成）

- `kernel/templates.py` 实现代码内固定 `orchpact-workflow-template`，首版提供通用 `json-delivery`，以版本和 canonical SHA-256 固定内容。`parameters_schema` 支持有界嵌套对象、字符串、整数/数字、布尔、标量数组、标题、说明、默认值、必填、范围和枚举；数组对象与任意额外属性不生成表单。
- 服务端递归应用默认值，复用现有 `validate()`、显式 `$param` 替换、`validate_definition()` 和固定组件目录展开。默认值与显式等价参数生成相同规范 JSON 和 SHA-256；缺失、额外、错误类型、未知参数引用、运行时字段与凭据注入全部拒绝。
- `GET /api/v1/workflow-templates` 与 `POST /api/v1/workflow-templates/{template_id}/preview` 仅限管理员且只读。预览固定模板摘要，返回解析参数、普通定义、最终 SHA-256 和可选组件报告，不创建工作流、应用、安装或执行。
- `static/kernel/template-form.js` 生成原生表单、读取嵌套/标量/数组参数并映射服务端字段错误，不实现另一套 Schema 或权限规则。管理台展示实际固定节点、依赖、文件和完整 JSON；模板或参数变化使旧预览失效。
- “使用此定义登记”只把定义带入现有发布编辑器。显式保存、应用绑定、Workspace 预览与安装仍是独立动作；模板不能填写真实模型、endpoint、Workspace、凭据或私有 Artifact ID，受控枚举仅表达逻辑引用。
- 首版没有远程模板、模板市场、拖拽编辑器或浏览器端权限判断。组件模板必须显式提供本地固定目录，HTTP 预览不读取隐式路径。

**验收结果**：`tests/test_templates.py` 的 6 项测试覆盖默认值等价、全部受支持参数类型、错误/注入拒绝、固定组件复用、管理员权限、预览只读和显式发布；`tests/template-form.test.cjs` 的 6 项测试覆盖控件读取、嵌套默认值、数字/布尔/数组、错误路径、转义和计划预览。本阶段全量回归实际通过 Python 345/345（含隔离 PostgreSQL）、Node 26/26、契约验证 7/7、scripted eval 36/36；scripted 结果不代表真实模型质量。

## 13. 阶段 9：文档交付参考应用（不进入 Core）

### 13.1 参考工具

- `project.query`：只读项目数据源；输出来源、时间、字段、权限过滤与数据摘要。
- `document.extract`：解析 DOCX/PDF/XLSX 中的文本、表格、元数据；标注来源与提取不确定性。
- `fact.extract`：可由 Agent/组件整理事实，输出 `confirmed / extracted / conflicted / missing` 状态及证据；关键事实需人工确认。
- `document.render`：确定性模板渲染，接收 `template_id`、`template_version`、事实 Artifact，返回 DOCX Artifact。
- `document.verify`：检查结构、样式、必需章节、表格、附件、事实引用；对严格分页/溢出还需真实渲染与视觉检查，不能只看 OOXML。LibreOffice 与目标 MS Word 可能存在差异，严格验收须在目标环境校验。
- `document.convert`：DOCX → PDF，记录转换器版本与环境。
- `delivery.package`：只打包已验收 Artifact，包含 manifest 与摘要。

### 13.2 工作流

```
project.query / document.extract
       ↓
fact.extract → 关键事实人工确认
       ↓
共享项目事实 Artifact
       ↓
并行 document.generate 组件实例 × N
       ├── 内容生成（必要时 Agent）
       ├── document.render（确定性工具节点）
       └── document.verify（强制工具节点）
       ↓
跨文档事实与版本一致性检查
       ↓
人工审批（按策略）
       ↓
delivery.package → 已验证交付包
```

- 不允许虚构部署截图、测试证据、系统状态；缺少真实证据应阻断交付或转人工补充。
- 初始验收用 3 份代表性文档（固定字段、叙述内容、跨文档依赖），至少 2 套脱敏项目资料；一套用于开发，另一套用于盲测。
- 文档业务代码必须放在独立参考应用/工具服务，通过公开工作流、应用签名、工具与 Artifact 边界接入；不得在 `kernel`、`platform` 或 `validation.py` 中硬编码 `docx` 规则，也不要求 Core 检出某个 sibling 仓库才能测试或运行。
- 参考应用的 DOCX/PDF 等依赖、LibreOffice/字体/转换服务和凭据由参考应用自行锁定与运维；不能为了参考实现给 Core 静默增加领域依赖。普通 Orchpact 测试仍只使用合成传输，不启动真实付费服务。

## 14. 测试矩阵与交付质量门槛

| 维度 | 必测场景 | 成功条件 |
|---|---|---|
| 向后兼容 | 旧 workflow 发布、安装、运行、导出 | 行为与摘要语义不退化 |
| 定义/绑定 | 缺槽位、额外槽位、错误版本、结构化秘密字段 | 全部拒绝 |
| 租户隔离 | 跨 Workspace/应用工具、文件、凭据访问 | 全部拒绝 |
| 团队兼容 | 多岗位工具能力、参与名单、Crew/attempt 限额 | 不共享越权工具，不刷新额度 |
| Agent 工具 | 授权工具、未知工具、非法参数、恶意输出 | 仅合法调用成功 |
| 确定性节点 | 无模型执行、组织汇总兼容、依赖阻断、失败恢复 | 不产生虚假模型调用，不误走汇总 |
| 外部副作用 | 超时、断连、重启、重复请求 | 不自动重放 UNKNOWN |
| Artifact | 文件过大、路径穿越、哈希不符、错误媒体类型 | 全部拒绝，现有限额不退化 |
| Verifier | PASS/FAIL/INCONCLUSIVE、伪造 PASS、证据缺失 | 仅有效证据可验收 |
| 验收 | 产物 ID/摘要/generation、契约或验证器版本变化 | 旧证据失效，partial 不冒充 completed |
| 返工 | 单文档失败、共享事实变化、返工额度耗尽 | 只重跑必要分支及上级汇总且有界 |
| 组件 | 双实例、嵌套、循环、参数错误、版本固定 | 确定性展开与拒绝错误 |
| 表单 | Schema 错误、权限错误、预览一致性 | 与底层校验一致，不合并独立修订动作 |
| 质量评测 | 真实脱敏样本、隐藏测试、人工盲评 | 单独报告错误接受率，不冒充绝对保证 |

每个阶段先运行受影响的现有回归，再运行仓库标准检查；涉及完整交付候选时按 `docs/RELEASE_VALIDATION.md` 执行：

```sh
uv sync --frozen
uv run orchpact workflow-check examples/workflows/department-review.json
uv run python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.cjs
uv run python evals/verify_contracts.py --output /absolute/new/contracts.json
uv run python evals/run.py --transport scripted --mode all --output /absolute/new/eval.json
git diff --check
```

scripted eval 只证明合成流程接通。新工具测试同样使用隔离 PostgreSQL 与本地 mock server，不访问外网或真实租户数据；真实模型质量、文档渲染效果、性能与目标环境兼容性另行验收。

### 必须区分的四个结果

1. **工具调用成功**：服务返回有效协议响应。
2. **验证器执行成功**：验证过程完整结束。
3. **验证检查通过**：指定检查结果为 PASS。
4. **交付被接受**：当前 Artifact + Contract + Evidence 满足 Acceptance Policy，并经过既有适用的独立/人工验收。

不能将这四种状态合并成一个 `success=true`。

### 建议质量指标（不是保证值）

- 未经授权工具调用成功数 = 0；
- 已知未通过阻断性验证却被正式接受数 = 0；
- 未知副作用自动重放数 = 0；
- 历史执行因新绑定/组件版本被静默改变数 = 0；
- 真实交付质量：报告人工修改次数、格式错误、事实冲突、错误接受率、执行成本、时延与返工次数；不预设一定提升。

## 15. 每阶段 AI 交付格式

每完成一个阶段，AI 必须交付：

1. **变更摘要**：做了什么、复用了哪些现有实现、不能复用时说明理由、是否改变公共协议。
2. **实际修改文件列表**：精确路径、关键类/函数、迁移文件。
3. **接口与数据示例**：合法输入、非法输入、输出与错误语义。
4. **兼容性说明**：旧定义、旧 API、旧快照、旧产物与数据库升级。
5. **安全/可靠性说明**：授权、凭据、隔离、幂等、未知结果、预算、验收。
6. **测试证据**：命令、通过/失败/跳过数量、日志摘要；未运行的测试须注明原因。
7. **未解决事项**：必须明确，不能用“已完成”掩盖风险。
8. **提交记录**：在用户要求提交时给出提交哈希与 Conventional Commit 消息；不自动部署、发布或升级安装。

## 16. AI 开发者总提示词（可直接复制）

你是 Orchpact 仓库的资深 Python/PostgreSQL/CrewAI 架构与实现工程师。请以本文为工程需求，但**以仓库实际源码与 AGENTS.md 为准**。目标是增量实现：工具声明与绑定、统一 Tool Runtime、Agent Tool Call、确定性工具节点、现有文件 Artifact 的工具导入与媒体契约扩展、可信 Verifier/Evidence/Acceptance、基于现有机制的局部返工、JSON 组件编译期展开、模板参数表单、独立文档交付参考应用。**不要引入 CUE**，不要重写现有执行引擎、CrewAI 团队任务/岗位行动循环、状态机或账务权限。严格保留工作流版本、应用绑定、Workspace 安装、run 来源与 work item/attempt 快照分离；团队声明可复用，Crew/Task/Agent 运行对象不跨执行共享。每阶段先提交设计和测试方案，确认现有代码可复用点，再进行最小修改；完成后运行可执行的全部回归测试，给出真实测试结果。对无法运行的 PostgreSQL 集成测试不得伪称通过。涉及不确定外部副作用必须阻止自动重放。所有验证结果绑定不可变 Artifact ID、摘要、generation 与契约摘要，未通过阻断性检查不得自动验收，现有 waive/partial 不得冒充完整接受。遵循本文阶段依赖，不提前开发市场、分布式调度、拖拽编辑器等非目标能力。完成阶段后提供路径级变更说明、测试结果、兼容性风险及按用户要求创建的提交记录，等待下一阶段确认。

---

**成功定义**：同一份固定版本的 JSON 工作流可以复用固定版本组件、调用被授权且可审计的外部工具，输出不可变产物，经可信验证证据和既有验收状态机确认后交付；任何未验证、验证不通过、证据过期或副作用结果未知的情况均不会被误当成成功。
