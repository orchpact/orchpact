# Orchpact · 序契

[简体中文](README.md) | **English**

Pronunciation **/ˈɔːrk.pækt/** (ORK-pakt).

**Orchpact is an open-source, self-hosted AI workflow service.** Define who does each step, what they deliver, and how to accept the result. Orchpact runs the workflow and records each delivery.

[Watch a delivery replay](https://orchpact.github.io/#demo) · [Get to your first delivery](https://orchpact.github.io/docs/quickstart/) · [Architecture](https://orchpact.github.io/docs/architecture/)

## Start locally

Install Docker Compose, Git, and Python 3, then run:

```sh
git clone https://github.com/orchpact/orchpact.git
cd orchpact
python3 docker/start.py
```

On first start, set a password for `admin` in the terminal. Open the printed address and sign in. In the canvas, open **工作流库 → 内置示例** (Workflow library → Examples), open the multi-warehouse fulfillment canvas, select **运行示例** (Run example), connect a text model when prompted, then download the JSON from **最终交付** (Final delivery).

## Try an example

The [department collaboration example](examples/workflows/department-review.json) uses an analyst and writer to produce a sourced total, then sends that artifact to an independent reviewer. On the canvas you can define stages, drag branches from a node and publish a selection as a reusable workflow. Publish the definition, preview and install its model bindings, then start a run from its installed canvas. To integrate a program, submit to `POST /api/v1/workflows/{id}/runs` with an idempotency key. See the [workflow protocol](https://orchpact.github.io/docs/workflows/) and [API tutorial](https://orchpact.github.io/docs/tutorials/api/).

From the source checkout, validate the definition and try the pass, rejection, repair, and inconclusive scenarios:

```sh
uv sync --frozen
uv run orchpact workflow-check examples/workflows/department-review.json
uv run python examples/verified-delivery.py
```

The delivery demonstration uses isolated PostgreSQL and synthetic model responses. [Example index and setup](examples/README.md) · [Machine-readable contracts](contracts/README.md)

## Development and project policies

Development uses Python 3.11–3.13, uv, Node, and isolated PostgreSQL. See [Contributing](CONTRIBUTING.md) for local checks and the [release verification guide](https://orchpact.github.io/docs/validation/) for candidate builds.

[MIT License](LICENSE) · [Third-party licenses](THIRD_PARTY.md) · [Security reports](SECURITY.md) · [Brand assets](src/orchpact/static/brand/README.md)
