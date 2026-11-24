# 直接运行工作流

Orchpact 的主流程不需要子应用、签名网关或产品域名。用户通过平台会话使用当前 Workspace，发布工作流，预览/安装资源，再提交输入。浏览器不会持有模型密钥；模型和工具调用继续由服务端执行。

所有路径带 `/api/v1` 前缀。管理接口使用个人管理员会话或服务端提供的管理员 Bearer Token；普通用户接口执行 Workspace 成员和所有者检查。不要将管理 Token 写入网页。

## 发布、安装、执行

1. 管理员 `POST /workflows?revision=0` 发布 `orchpact-workflow` 定义；更新时 revision 为当前目录头修订。
2. `POST /workflows/{id}/preview` 提交明确的 `workflow_revision` 和本地 `bindings`，只读校验能力与工具授权。
3. 所有者或管理员 `POST /workflows/{id}/install` 提交同样内容及预览返回的 `expected_revision`，显式更新当前 Workspace 安装。安装修订发生冲突返回 409。
4. `POST /workflows/{id}/runs` 提交 `{operation,input,context}`，必填 `Idempotency-Key`。实际使用当前空间已安装的明确修订，不隐式跟随目录 latest。
5. `GET /runs/{id}` 查询状态、产物、验收、证据、用量和可重试工作。

新发起的直接执行默认要求所选操作声明固定 `execution_plan`，否则返回 409 `fixed_plan_required`。有意试验自主规划时，在实例的 `config/execution.toml` 中设置 `[execution]` 下的 `allow_experimental_planning = true` 并重启。已受理执行、幂等回读及历史兼容入口沿用原行为。

安装请求示例：

```json
{"workflow_revision":1,"expected_revision":0,"bindings":{"models":{"reasoning":"your-text-profile"}}}
```

管理员提供者配置中的工具策略可以使用 `workspace_ids` 和 `workflow_ids` 限制范围。旧 `application_ids` 仅用于历史执行范围，不能替代新工作流授权；已有策略不会被自动放宽。定义只引用逻辑模型/工具槽位，不填写 endpoint、凭据、用户或 Workspace。

发布不改变已安装版本；安装升级不改变旧执行。相同 Workspace、工作流、幂等键在安装升级前后仍返回原执行，内容不同返回 409，不再次扣费。模型、工具、付款人、价格和验收规则在既有快照边界固定。

```sh
uv run python examples/register-workflow.py examples/workflows/department-review.json \
  --install --bindings /absolute/private/bindings.json
```

客户端从环境读取 `ORCHPACT_API_TOKEN`；`--update` 才发布新修订，`--install` 才安装，登记与安装不会发起模型调用。管理台工作流目录也提供预览安装和直接运行。

## 查询与控制

| 操作 | 接口 |
| --- | --- |
| 当前空间安装 | `GET /workflows/{id}/installation` |
| 当前空间该工作流的历史执行 | `GET /workflows/{id}/runs?limit=100` |
| 执行与交付 | `GET /runs/{id}` |
| 取消 | `POST /runs/{id}/cancel` |
| 明确重试 | `POST /work-items/{id}/retry`，附幂等键 |
| 计划/验收命令 | `POST /runs/{id}/commands` |
| 回答问题 | `POST /questions/{id}/answers` |
| 用量与调用 | `GET /runs/{id}/usage`、`/requests` |
| 事件 | `GET /runs/{id}/events` |
| 供应商回执 | `GET /requests/{id}` |
| 验证证据 | `GET /verification-evidence/{id}` |
| 产物元数据与下载 | `GET /artifacts/{id}`、`/download`、`/preview` |
| 尝试 | `GET /work-items/{id}/attempts` |
| 工具 UNKNOWN 只读对账 | `POST /tool-invocations/{id}/reconcile` |
| 读取待审批外部写操作的目标和参数 | `GET /runs/{id}/tool-approvals/{approval}` |
| 批准或拒绝一次外部写操作 | `POST /runs/{id}/tool-approvals/{approval}/decision`，提交 `{approval_digest,decision}`，其中 `decision` 为 `approve` 或 `reject` |

执行视图的 `tool_approvals` 列出审批摘要，参数详情及决定只对当前 Workspace 的所有者和管理员开放。批准会将工具节点重新排队。直接执行前仍会复查授权、预算和幂等记录。拒绝、取消或旧代次失效均不能授权发送；已使用的审批关联实际 `tool_call_id`。

`result` 仅在完整验收后返回；二进制交付用 `artifact_id` 下载。声明 verification 的候选保持 running/pending，PASS 证据落库后才可接受。FAIL/INCONCLUSIVE 保留候选及证据；UNKNOWN 不自动重放。waive 只能得到 partial/waived。工作项的 `requires_independent_review` 来自冻结策略，不能通过改变当前策略回写历史。

回执、`accounting`、`tool_invocations`、`verification_evidence`、`crew_runs` 和 `agent_steps` 与执行采用相同的 Workspace 授权。未知用量保持未知，账务核定不等于交付验收。证据绑定产物 ID/SHA-256/generation、契约摘要、验证器版本和 tool_call_id。工具参数和供应商原文不加入公开步骤列表。

## 子应用功能移除与历史兼容

已删除子应用绑定创建、公钥/签名认证、网关实现、产品域名及前端托管。新 CLI 不再接受 `--application` 或网关密钥参数，管理台没有应用绑定或公钥管理页。携带旧 `X-Orchpact-*` 签名头的请求明确拒绝，不能回退成普通会话。原 `products/frontends` 非空配置会阻止启动并要求先移出，不会悄悄暴露产品页面。

旧 `/apps/{id}` 的执行、安装及文档接口作为兼容适配保留但不进入公开 OpenAPI；旧 `/organization-packages` 仍标记 deprecated。它们不是新的推荐接入方式，既有签名要求不会因移除签名网关被解除。旧受保护范围的历史应由有权限的管理员查看或导出。

`0004_direct_workflows` 只新增 Workspace 工作流安装索引。历史应用表及外键继续作为内部存储范围存在，旧运行、文件、回执、账务和审计不删除、不改写；这些内部记录不要求用户创建子应用。迁移由现有持锁、先备份的执行器运行。数据库与配套文件仍须成对备份恢复。
