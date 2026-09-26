# Handover: Marvento Ads MCP

Rules: see AGENTS.md.
Last updated: 2026-09-27 00:20 Asia/Dubai by Luis via Claude.
Current version: unreleased (no deployment yet). Baseline upstream d7f5e8f.

## Current state
Code complete and unit-tested locally (100 tests pass). Server verified locally in HTTP + OAuth mode: `/.well-known/oauth-authorization-server` served, `/mcp` returns 401 with `WWW-Authenticate` until a client logs in. Nothing deployed yet; no Google Cloud project yet.

## Work in progress
- Deployment (Claude, with Luis providing credentials): `deploy/create_droplet.py` ready; waiting on a DigitalOcean API token and the Google OAuth client id/secret.
- Google side (Luis): Cloud project, Ads API enabled, OAuth consent (published to production), OAuth web client with redirect `https://ads-mcp.mlabs.ae/auth/callback`, brand verification, Basic access application, test manager account.

## Blockers
- Google API access level: production account calls fail until the Cloud project holds Explorer or Basic. Timing is Google's (brand verification, then automated review).
- Droplet creation: needs the DO token.

## Next steps (priority order)
1. Repo: `MarventoCapital/marvento-ads-mcp` (public; upstream history kept via git push). Set `GIT_REPO` when running `create_droplet.py` if it moves.
2. Run `deploy/create_droplet.py` once the DO token and OAuth client exist; create the Cloudflare A record (DNS only) if no `CF_TOKEN` is provided.
3. Add the custom connector in claude.ai (`https://ads-mcp.mlabs.ae/mcp`), sign in with Google, confirm 23 tools are listed.
4. Smoke test on the Google Ads **test** account: `customers_list_accessible_customers`, then budget -> campaign -> targeting -> ad group -> keywords -> RSA -> sitelinks -> `set_campaign_status ENABLED confirm=true`, and read it all back with `search_search`.
5. When Explorer/Basic is granted: list accessible customers on the live account and run a read-only performance report. Then cut release 0.1.0 in CHANGELOG.md with the deployment reference.
6. Later: keyword ideas tool (needs Basic), Performance Max support, scheduled reporting.

## Open questions for the owner
- Budget cap: default is 500/day in account currency. Raise it in `/opt/ads-mcp/.env` when a campaign needs more.
- Should other Google users (e.g. Sara) be able to connect? Any Google account that can see the Ads account can sign in; restrict at the Google Ads user level.

## Found undocumented
None.

## Environment and access notes
- Production URL: https://ads-mcp.mlabs.ae (MCP endpoint `/mcp`, OAuth callback `/auth/callback`).
- Droplet: DigitalOcean, name `ads-mcp`, tag `ads-mcp`, region fra1, reserved IP (see `create_droplet.py --status`).
- Secrets: `/opt/ads-mcp/.env` on the droplet only. Names in `deploy/.env.example`.
- Google Cloud project: `marvento-labs-ads` (planned id; confirm in console). OAuth client "Marvento Ads MCP".
- DNS: Cloudflare zone mlabs.ae.
- Repo: github.com/MarventoCapital/marvento-ads-mcp (fork of googleads/google-ads-mcp).
