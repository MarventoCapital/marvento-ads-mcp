# Changelog

All notable changes to this project. Format follows Keep a Changelog; versions follow semantic versioning.
Newest first. Never edit past entries; add a Correction entry instead.

Fork baseline: upstream googleads/google-ads-mcp commit d7f5e8f (2026-09-25, package version 0.0.4).

## [Unreleased]

### Added
- Write tools in four new namespaces: `campaigns`, `ad_groups`, `ads`, `assets` (20 tools). Why: upstream is read-only; running campaigns from an MCP client needs create/update. Files: `ads_mcp/tools/campaigns.py`, `ad_groups.py`, `ads.py`, `assets.py`, shared helpers in `ads_mcp/mutate.py`. Tested: 33 unit tests in `tests/tools/write_tools_test.py` covering request shape and safety gates; full suite 100 tests pass.
- Safety rails: campaigns created PAUSED; `confirm=true` required for ENABLED/REMOVED, budget and bid changes, criterion removal; `ADS_MCP_MAX_DAILY_BUDGET` cap (default 500); `validate_only` on every tool; local validation of RSA copy, sitelinks and callouts. Why: an agent mistake must not be able to start or scale spend on its own.
- Deployment kit in `deploy/`: `docker-compose.yml` (server + Caddy auto-HTTPS), `Caddyfile`, `cloud-init.yaml`, `create_droplet.py` (DigitalOcean reserved IP + droplet + optional Cloudflare A record), `update.sh`. Why: the server has to be reachable as a remote connector from claude.ai, so it lives on a VPS rather than a laptop.
- Root continuity files (AGENTS.md, CLAUDE.md, CHANGELOG.md, DOCUMENTATION.md, HANDOVER.md) and a fork section at the top of README.md.

### Changed
- `ads_mcp/config.py`: `ALL_CATEGORIES` extended with the four write namespaces; `ads_mcp/tools_config.yaml` enables them by default.
- `pyproject.toml`: `fastmcp` pinned to `==4.0.3`. Why: the Dockerfile patches that exact version and would fail the build on a silent upgrade.
- `.gitignore`: ignores `.env` and `deploy/.env`. `.dockerignore` added.
- Removed upstream-only files: `.github/` CI and release workflows, CODEOWNERS, CONTRIBUTING.md, docs/releasing.md, tests/smoke/. Why: they target Google's repo and PyPI release process.

### Fixed
- `pyproject.toml`: setuptools package discovery restricted to `ads_mcp*`; `.dockerignore` now excludes all of `deploy/`. Why: with `deploy/create_droplet.py` present, setuptools auto-discovery found two top-level packages and refused to build, so the Docker image (and any `pip install .`) would have failed on the droplet. Caught by a clean install from the GitHub clone. Tested: fresh venv install, 100 tests pass, 23 tools mount from the installed package, simulated Docker build context installs and the Dockerfile's FastMCP patch assertion holds.
- `deploy/create_droplet.py`: retries the reserved-IP assignment while DigitalOcean still reports the droplet's create event as pending (422), and adds `--recreate` (destroy + create on the same reserved IP, so DNS stays valid). Why: secrets on the droplet can only be changed by rebuilding it, since this workspace has no SSH path; the placeholder-credential test deployment relies on it. `deploy/cloud-init.yaml`: swap size given in bytes, the form cloud-init documents.

### Repository
- Published to github.com/MarventoCapital/marvento-ads-mcp (public) on 2026-09-27 through the GitHub connector. History there starts fresh (8 commits); upstream history stays in googleads/google-ads-mcp. Verified: clone is byte-identical to the working tree (60 files).

Signed: Luis via Claude, 2026-09-27.
