# Orchpact · 序契

**简体中文** | [English](README.en.md)

建议读音（美式） **/ˈɔːrk.pækt/**（ORK-pakt）。

**Orchpact 是开源、可自托管的 AI 工作流服务。** 定义每一步谁来做、交付什么、怎样才算通过。Orchpact 按规则推进，并记录每次交付的结果。

[看一次交付](https://orchpact.github.io/#demo) · [从安装到第一份交付](https://orchpact.github.io/docs/quickstart/) · [系统架构](https://orchpact.github.io/docs/architecture/)

## 本机启动

准备 Docker Compose、Git 和 Python 3。运行以下命令：

```sh
git clone https://github.com/orchpact/orchpact.git
cd orchpact
python3 docker/start.py
```

首次启动在终端为 `admin` 设置密码。启动完成后，打开终端显示的地址并登录，进入画布，在 **工作流库 → 内置示例 → 多仓订单履约** 打开示例画布，点 **运行示例**。按提示连接一个文本模型，运行结束后在 **最终交付** 下载结果。

教程包含每一步的操作说明、预期结果和排错入口。[入门教程](https://orchpact.github.io/docs/quickstart/)

## 运行示例

[部门协作示例](examples/workflows/department-review.json)让分析员和撰稿员先汇总来源，再由独立团队核验结果。可以在画布自定义环节、从节点拉出分支、选中一组节点封装成工作流。发布定义并安装模型资源后，在已安装画布点「发起运行」。用程序调用时走 `POST /api/v1/workflows/{id}/runs`，并保留幂等键。详见 [工作流协议](https://orchpact.github.io/docs/workflows/)与 [API 教程](https://orchpact.github.io/docs/tutorials/api/)。

在源码目录验证定义和观察通过、拒绝、返工与证据不足：

```sh
uv sync --frozen
uv run orchpact workflow-check examples/workflows/department-review.json
uv run python examples/verified-delivery.py
```

交付演示需要隔离的 PostgreSQL，使用模拟模型响应。[示例索引与准备步骤](examples/README.md) · [机器可读契约](contracts/README.md)

## 开发与项目政策

使用 Python 3.11–3.13、uv、Node 和隔离的 PostgreSQL。安装、测试、贡献与候选发行见 [贡献指南](CONTRIBUTING.md) 与 [发行验收](https://orchpact.github.io/docs/validation/)。

[MIT 许可证](LICENSE) · [第三方许可](THIRD_PARTY.md) · [安全报告](SECURITY.md) · [品牌素材](src/orchpact/static/brand/README.md)
