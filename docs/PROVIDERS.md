# 提供者、积分与凭据

管理员进入 `/admin/#providers`。`GET/PUT /api/v1/admin/configuration` 使用 `{revision,value}`，历史由 `/admin/configuration/history` 读取。保存冲突返回 409；后台保存后以数据库为准，不被 TOML 静默覆盖。

文本默认 1000 Token / 积分，搜索默认 10 积分 / 请求，图像初始按次 100 积分；均是内部折算。提供者可以设置独立价格（最多六位小数），0 表示明确免费。没有完整 Token 的按 Token 调用保留预留，不能因为请求失败而自动免费。人工核定保留原始未知用量。

调用快照包含模型、输入、输出契约、价格和凭据引用。调价不重算历史；配置修改不重写已创建任务。当前授权、提供者停用、预算与原付款人余额在新请求前重新检查。搜索与模型能力分开授权，模型支持 search 不代表角色可以使用。

提供者 secret/secondary_secret 只写入。托管密钥文件位于实例 `var/provider-secrets`，目录 700、文件 600，以不可变引用保存旧配置；它们是敏感明文文件，必须与 PostgreSQL dump 和配套文件标识一并安全备份，参见 [PostgreSQL 运维](POSTGRESQL.md)。API、目录、审计和模型上下文不回显凭据。环境变量和 `.env` 另行备份。

并发配置 1–64，重启后生效，项目并发不超过总线程数。管理面允许零模型启动；实际安装/委托选择能力时缺失会失败。初始 TOML 中 `enabled="auto"` 只启用有凭据的配置。

响应解析和落盘受尺寸限制，但部分 HTTP 适配器会先缓冲响应，尚未提供所有供应商统一的流式内存硬限制。不能用模拟响应宣称模型质量、真实检索能力或实际图像生成通过。

## 可信工具提供者

同一 providers 列表支持 `category="tool"`，`adapter` 可登记为 `http`、`mcp` 或 `builtin`；当前 Runtime 仅执行 HTTP 和代码内静态 builtin，MCP 在传输与 SDK 依赖评审前拒绝绑定。工具记录额外包含：

- `tool_interface` 与 `tool_interface_version`：必须匹配工作流逻辑声明；
- `implementation_version`：安装绑定必须明确选择该版本，不使用 latest；
- `tool_policy.workspace_ids` / `workflow_ids`（旧范围兼容 `application_ids`）：非空列表只允许列明范围，空列表表示不限制相应范围；
- `tool_policy.require_credential`：为 true 时，未配置托管秘密的提供者不能绑定；
- `tool_policy.max_concurrency`：同一 provider 的 started 调用上限，1–64；
- `tool_policy.network`：`allowed_hosts`、`allowed_ports` 和 `allow_private`。原始 endpoint 主机总是需要逐次 DNS/IP 检查，重定向主机还必须在白名单中；私有地址默认拒绝。

HTTP 地址使用无内联凭据、无 query/fragment 的 HTTPS URL；builtin 地址为空。HTTPS adapter 每次连接及每次重定向都解析并检查全部 IP，直接连接已验证地址，同时用原域名执行 SNI 和证书校验，默认不使用环境代理。重定向最多 3 次，跨主机不发送 Authorization，external_write 不跟随重定向。响应按字节流式限制，另有总 deadline；系统 DNS 解析器本身的超时仍取决于部署环境。测试使用合成 DNS/连接，不宣称完成真实公网安全评估。

工具只使用 `secret` 写入的托管秘密，不使用环境变量凭据，以便旧 run 和明确重试继续解析原秘密引用。工具价格必须明确配置为按 request 的内部积分，`points=0` 是免费，pricing 缺失会在任何外部调用前拒绝；这不是供应商币种金额。成功和确定失败的请求按冻结价格结算，UNKNOWN 保持预留，等待真实回执、只读 lookup 或人工核定。

配置摘要覆盖接口、实现、endpoint 和策略，不覆盖托管秘密引用。语义配置变化后，旧安装不能发起新 run，须登记新应用修订并显式升级；仅轮换秘密时，新 run 使用新引用，旧 run 和重试仍使用旧引用。公开安装/run 元数据不包含 URL、秘密明文或 `_secret_ref`。Runtime 参数、结果与回执位于私有 `var/tool-invocations/`，数据库和 run 审计只保存状态、引用和摘要。

## 可信 Verifier 配置

只读工具可增加 `tool_verifier`，包含 `checks`、`media_types`、`trust_level` 和 `environment`。只有管理员显式设置 `trust_level="trusted"` 的 provider 才能用于交付验收；`checks` 必须覆盖工作流全部 required_checks，`media_types` 必须覆盖被验证产物。该元数据进入安全配置摘要和 run/工作项快照，修改后需要新应用修订和 Workspace 显式升级。普通工具省略或为 null，不改变既有配置摘要。

Verifier 执行结果仍需通过平台语义检查：HTTP 成功、工具自报 PASS 或不完整检查集都不能替代真实 PASS 证据。证据位于 PostgreSQL `verification_evidence` 和配套 `var/verification-evidence/`，备份恢复必须包含两者。

## CrewAI 的模型与工具适配

CrewAI 的 BaseLLM 桥接器通过本平台 Providers 发起每次实际模型请求，不直接读取供应商密钥或选择新的付款人。工具通过平台 BaseTool 封装访问搜索或当前依赖产物，每次执行重新检查授权。框架的 usage_metrics 不用于扣账，实际回执和请求预留才是账务依据。职责和边界见 [执行设计](EXECUTION_BOUNDARY.md)。
