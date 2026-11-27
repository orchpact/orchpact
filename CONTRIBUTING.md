# Contributing

Use Python 3.11–3.13 and uv. The console and sign-in use React with shadcn/ui. Design and run views share @xyflow/react. The wheel and Docker image serve the included browser assets. To change them, use Node 22.13+ and npm from the repository root.

```sh
npm ci --prefix frontend
npm run --prefix frontend build
npm run --prefix frontend check
python3 design/identity/validate.py
python3 scripts/sync_identity.py --check
```

After updating the versioned design kit, run `python3 scripts/sync_identity.py` to copy the approved assets to their public paths.

Tests require real PostgreSQL: install local server binaries and set ORCHPACT_PG_BIN, or use ORCHPACT_TEST_DATABASE_URL pointing to a dedicated test server with CREATEDB permission. Set ORCHPACT_PG_DUMP to a matching client. Never point the test server at production. See [isolated PostgreSQL test setup](https://orchpact.github.io/docs/postgresql/#测试与-ci).

```sh
uv sync --frozen
uv run python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.cjs
uv run python evals/run.py --output /tmp/orchpact-eval.json
```

Keep domain contracts in kernel, orchestration in services, transport/storage in adapters, and consumer business rules in consuming applications or portable workflow definitions. Both native plans and model proposals must use the same command/graph implementation.

Use temporary instances and mocked external transports in tests. Do not invoke paid providers, execute generated code, install generated dependencies, or deploy generated output during ordinary checks. Separate deterministic verification from real model quality evidence.

Preserve immutable prompts, receipts, payer and pricing boundaries. Authorization, billing and file changes need behavior regressions. Update public contracts and docs when behavior changes. Never commit .env, runtime databases, private keys, sessions, operational artifacts or user material.

Public compatibility boundaries are in [the compatibility guide](https://orchpact.github.io/docs/compatibility/). Candidate snapshots use the reviewed [release/files.json](release/files.json) allowlist; see [release validation](https://orchpact.github.io/docs/validation/). Keep private operational evidence outside that snapshot.

This project is available under the MIT License. Preserve third-party license notices when changing vendored assets.

## Commit messages

Use Conventional Commits: `type(scope): description`. Keep the subject within 72 characters; scope is optional. Use `!` and a `BREAKING CHANGE:` footer when a public contract changes incompatibly.

- `feat(workflows): add resource binding preview`
- `fix(billing): preserve reservations for unknown results`
- `docs: explain application isolation`

Allowed types: `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `test`. Describe the resulting change without private product names or internal iteration labels. Keep one coherent purpose per commit. Squash PRs use the same format.

Enable the repository hook with `git config core.hooksPath .githooks`. CI checks submitted commit subjects and PR titles; run `python3 scripts/check_commits.py --base BASE_SHA --head HEAD` locally for a range.

CrewAI owns team task execution and each member agent loop. Teams are reusable declarations; runtime Crew/Task/Agent objects are never shared across runs. Keep cross-task orchestration, authorization, billing, receipts and artifact acceptance in Orchpact. All model/tool effects must pass through platform adapters; see [execution boundaries](https://orchpact.github.io/docs/execution/). Agent integration tests must run real CrewAI with synthetic external transports.
