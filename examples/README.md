# 可测试的交付场景

## 1. 金额汇总与独立验收：无需真实模型

从仓库根目录运行：

```sh
uv sync --frozen
uv run python examples/verified-delivery.py
# 只看一次修复，或保存新的 JSON 结果文件：
uv run python examples/verified-delivery.py --scenario repair --output /tmp/orchpact-repair.json
```

要求本地 PostgreSQL 二进制（可设置 `ORCHPACT_PG_BIN`），或专用测试服务器的 `ORCHPACT_TEST_DATABASE_URL` 与匹配的 `ORCHPACT_PG_DUMP`。脚本创建临时实例和独立测试库，不读取业务实例配置、管理 Token 或真实模型凭据；退出后清理。已有输出文件拒绝覆盖。

输入为两条记录 `3 + 5`。实际 CrewAI、模型适配、预算/账务、候选文件、Verifier、证据和验收运行在真实临时存储上；模型 HTTP 响应被合成传输拦截，不联网、不触发付费模型。Verifier 读取冻结输入并独立求和，不信任候选自报成功。

| 场景 | 候选 | 预期结果 |
| --- | --- | --- |
| `pass` | 总数 8 | PASS，接受，返回 total=8 |
| `fail` | 总数一直为 9 | 两次 FAIL，用尽一次返工额度，拒绝交付 |
| `repair` | 先 9，后 8 | 保留 FAIL/PASS 两版证据，第二代接受 |
| `inconclusive` | 无可确认结论 | INCONCLUSIVE，拒绝交付，不自动返工 |

报告列出 accepted、status、result、调用次数、返工次数及每版证据的 Artifact ID/摘要/generation。默认四个场景均断言预期行为；任一不符合则非零退出。`synthetic_model=true`、`quality_evidence=false`，用于观察平台机制，不能作为真实 Agent 质量提升的证明。

## 2. 多岗位分析与独立审校：使用自己的模型

`workflows/department-review.json` 展示两个团队：分析岗位与写作岗位交付结果，独立审校团队检查当前版本，拒绝时有界返工。

在管理台配置 text/json 模型和执行账号预算，登记定义，预览并安装 `reasoning` 槽位，然后在工作流目录直接运行 `calculate`：

```json
{"operation":"calculate","input":{"records":[{"id":"a","amount":3},{"id":"b","amount":5}]},"context":{}}
```

预期 total=8、source_ids 包含 a/b，并有独立审校记录。此场景会调用你的模型并消耗额度；模型审校不等同于程序验算。无需子应用、签名密钥或产品网关。

## 3. 显式选源与引用闭包

`workflows/explicit-reference-report.json` 展示资料整理、最多选择两个来源、报告撰写三个显式节点。`generate` 输入：

```json
{"sources":[{"id":"S1","text":"第一条资料"},{"id":"S2","text":"第二条资料"},{"id":"S3","text":"第三条资料"}]}
```

可检查来源数量上限、伪造 ID、成稿是否保留选择结果、正文是否引用清单之外的编号。流程验证结构和引用关系，事实是否真实、结论是否被来源支持仍需专业 Verifier 或人审。运行真实模型同样需要显式配置和预算。

## 4. 复杂业务：多仓订单履约

[完整场景](fulfillment/README.md) 包含 3 仓、8 订单、分批到货、承运商/仓库日容量、截止期和预算，经过 8 个阶段及根产物验收。运行 `uv run python examples/fulfillment/run.py` 可测试正常、库存修复、预算修复、最终交付篡改与证据不足五种情况；默认不调用真实模型。也提供显式 live 参数和独立候选校验命令。

## 5. 创作与人审：互动悬疑短篇

[《闭馆后的第七声钟响》](mystery-story/README.md) 使用人物秘密、三条线索、六个场景和双结局，经过创作、独立编辑与人工验收。运行 `uv run python examples/mystery-story/run.py` 可观察知情边界错误、不可达分支、线索未回收、编辑未决，以及“正文泄密但声明合法”的程序盲区。即使规则全部通过，根产物仍等待人审；不会把模型评分当成文学质量证明。
