# Agent notes for marvento-ads-mcp

## Project continuity (read before working)

This project keeps its memory in three root files. Keep them current so anyone can pick up the work.

- CHANGELOG.md: release history and patch notes. Finished but undeployed work goes under Unreleased.
- DOCUMENTATION.md: how the project works, what changed and why, decisions and their reasons.
- HANDOVER.md: current state, work in progress, blockers, next steps.

Before editing: read HANDOVER.md, check for changes by others since the last handover, and do not touch an area someone else is mid-work on without asking.
After every deployment or publish, successful or failed: record version, environment, date, deployment reference, what was verified and rollback steps in CHANGELOG.md, and update the affected DOCUMENTATION.md sections.
Before stopping: update HANDOVER.md so the next person can continue without you.
Always: say why, not only what. Never delete or rewrite past entries; add a correction. Never write secrets into these files; name the env var or vault entry instead. Sign entries with your name or tool.

## Code rules specific to this repo

- Upstream is `googleads/google-ads-mcp`. Keep upstream files untouched where possible so `git merge upstream/main` stays clean. Fork-specific code lives in `ads_mcp/mutate.py`, `ads_mcp/tools/{campaigns,ad_groups,ads,assets}.py`, `deploy/`, and the root docs.
- Every write tool goes through `ads_mcp.mutate.run_mutate` and keeps the rails: create PAUSED, `confirm` gate on spend/removal, budget cap, `validate_only`.
- Tests: `python -m unittest discover -s tests -p "*_test.py"`. Add a test in `tests/tools/write_tools_test.py` for every new write tool (request shape + gate behaviour).
- Never commit `.env` files or credentials. Secrets live only in `/opt/ads-mcp/.env` on the droplet.
