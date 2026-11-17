# Contributor instructions

Orchpact is an independent self-hosted workflow engine. Source lives in `src/orchpact`; tests and examples must run from this repository alone.

- Read relevant code, tests and docs; preserve unrelated changes.
- Use `uv sync --frozen`. Test with `uv run python -m unittest discover -s tests -p 'test_*.py'`, `node --test tests/*.test.cjs`, and `git diff --check`.
- Tests require isolated PostgreSQL: local binaries via `ORCHPACT_PG_BIN`, or a dedicated `ORCHPACT_TEST_DATABASE_URL` with CREATEDB and a matching `ORCHPACT_PG_DUMP`. Never use an application database.
- Preserve server-side authorization, application and Workspace isolation, immutable execution/pricing snapshots, original payer, idempotency, receipt recovery, and file limits. Never automatically replay unknown paid results.
- One main process owns each PostgreSQL database and paired file directory. Back up both together; never remove instance volumes during routine maintenance.
- Never commit credentials, `.env`, runtime state, uploads, private operational evidence or user content. Do not execute generated code or invoke paid providers in ordinary tests.
- Consumer business rules belong in the consuming application or its workflow definition. Do not add consumer-specific modules or sibling checkout dependencies.
- Frontends use native HTML/CSS/JavaScript. Technical docs originate here; the independent website imports committed snapshots. See `docs/REPOSITORIES.md`.
- Use Conventional Commits as described in `CONTRIBUTING.md`. Do not publish packages, deploy, or change workflow installations as an incidental code-editing step.

- CrewAI executes declared teams through fresh Crew/Task/Agent objects per invocation. Use its public runtime interfaces and the narrowly versioned run-local log adapter; do not copy its engine or create a parallel agent loop. Keep cross-task commands, permissions, payer, billing and acceptance in Orchpact. See docs/TEAM_EXECUTION.md and docs/EXECUTION_BOUNDARY.md.
