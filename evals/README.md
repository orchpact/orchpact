# 组织方式对照评估

同一内核、同一模型、相同输入/输出契约和每模式相同内部积分额度，比较 `single` 单角色、`fixed` 固定计算/独立审校、自主招募分派的 `autonomous`。评估不预设协作一定更好。

```sh
uv run python evals/run.py --transport scripted --mode all --output /tmp/orchpact-eval.json
```

存储使用真实隔离 PostgreSQL，与业务连接 `ORCHPACT_DATABASE_URL` 分开。需安装本地 PostgreSQL 并设置 `ORCHPACT_PG_BIN`，或指定专用 `ORCHPACT_TEST_DATABASE_URL`（CREATEDB）及 `ORCHPACT_PG_DUMP`；退出时只删除本次创建的测试库。live 模式也遵循相同隔离规则。

默认 scripted 运行 12 个公开的算术/引用用例（含资料注入文本），实际创建项目、授权、尝试、产物和验收，但响应与 Token 数均是合成夹具。输出明确标记 `synthetic=true`、`quality_evidence=false`。这只证明流程接通，不能证明模型质量、真实费用或注入防御有效。

明确选择 live 才发送真实付费请求：

```sh
uv run python evals/run.py --transport live --root /private/eval-config --budget-points 100 --repetitions 3 --output /private/results/run-01.json
```

使用专门配置目录、经批准的供应商额度和脱敏材料。运行数据写临时独立实例，不使用该目录的原业务数据库；后台托管模型凭据只在进程内转为临时环境引用。结果只记录模型标识、输出、状态、调用数、内部积分、耗时与评分，不导出凭据。内部积分不代表供应商金额，预留不足会停止，未知用量保持未结算。

先固定 cases、模型、预算、重复次数、提示词和评分标准，再观察结果。当前公开集仅是小规模算术/引用基线，不足以支持广泛的组织效率结论。扩展真实内容质量样本时，应增加未用于调提示词的保留集和盲评；分别报告正确率、漏引、错误接受、成本、延迟、人工介入与失败，不能只公开成功项。

能力缺口、计划变更、隐藏时长缺陷、重启未知结果、权限撤销与定向返工由确定性回归和真实产品集成夹具覆盖，见 [验收流程](https://orchpact.github.io/docs/validation/)。本轮没有运行 live。


## 执行契约复现

源码检出后运行：

```sh
uv run python evals/verify_contracts.py --output /tmp/orchpact-contracts.json
```

覆盖委托边界、当前产物验收、未知结果恢复和幂等结算四类共 7 项精选回归。脚本调用现有测试，在真实隔离 PostgreSQL 与合成供应商传输上运行；需要上述 PostgreSQL 测试配置，不读取业务实例凭据，不触发付费调用。任一失败、跳过或数量不符均返回失败；已有输出文件会拒绝覆盖。

JSON 保留分类、测试标识、数量、失败和耗时，明确 `quality_evidence=false`。它是可复现的程序契约检查，不是模型质量、性能或竞品对测报告。完整回归仍需按贡献指南运行；本入口也已加入 CI，远端运行结果另行核验。
