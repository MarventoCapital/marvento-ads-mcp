# Handover: Marvento Ads MCP

Rules: see AGENTS.md.
Last updated: 2026-09-27 14:35 Asia/Dubai by Luis via Claude.
Current version: unreleased (no deployment yet). Baseline upstream d7f5e8f.

## Current state
Code complete, on GitHub (MarventoCapital/marvento-ads-mcp, public, main). Clean install from the GitHub clone passes 100 tests and mounts 23 tools; simulated Docker build passes. Server verified locally in HTTP + OAuth mode: `/.well-known/oauth-authorization-server` served, `/mcp` returns 401 with `WWW-Authenticate` until a client logs in. Nothing deployed yet; Google Cloud project not confirmed yet.

## Work in progress
- Deployment (Claude): test droplet `ads-mcp` (id 604071109, fra1, s-1vcpu-1gb) created 2026-09-27 10:30 UTC on reserved IP 159.89.212.185 with PLACEHOLDER OAuth values, to validate cloud-init, the Docker build, Caddy/TLS and DNS before the Google side is ready. Waiting on: Cloudflare A record `ads-mcp` -> 159.89.212.185 (DNS only), then the real OAuth client ID/secret for `--recreate`.
- Google side (Luis): Cloud project, Ads API enabled, OAuth consent (published to production), OAuth web client with redirect `https://ads-mcp.mlabs.ae/auth/callback`, brand verification, Basic access application, test manager account.

## Blockers
- Google API access level: production account calls fail until the Cloud project holds Explorer or Basic. Timing is Google's (brand verification, then automated review).
- Resolved 2026-09-27: Cowork cloud sessions could not reach `api.digitalocean.com` (org egress policy, 403 on CONNECT). Luis added `api.digitalocean.com`, `api.cloudflare.com` and `ads-mcp.mlabs.ae` to the org domain allowlist (Organization settings -> Capabilities -> Code execution -> Domain allowlist); it applied to the running session.
- Real Google OAuth client not created yet: the droplet runs with placeholder OAuth values, so Google sign-in cannot work until it is recreated with the real client (`create_droplet.py --recreate`).

## Next steps (priority order)
1. Done 2026-09-27: repo published at `MarventoCapital/marvento-ads-mcp` (public). `create_droplet.py` defaults to it; set `GIT_REPO` if it moves.
2. When the real OAuth client exists: `DO_TOKEN=... GOOGLE_ADS_MCP_OAUTH_CLIENT_ID=... GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET=... GOOGLE_ADS_MCP_JWT_SIGNING_KEY=<same as before> python3 deploy/create_droplet.py --recreate`. Keeps the reserved IP and DNS.
3. Add the custom connector in claude.ai (`https://ads-mcp.mlabs.ae/mcp`), sign in with Google, confirm 23 tools are listed.
4. Smoke test on the Google Ads **test** account: `customers_list_accessible_customers`, then budget -> campaign -> targeting -> ad group -> keywords -> RSA -> sitelinks -> `set_campaign_status ENABLED confirm=true`, and read it all back with `search_search`.
5. When Explorer/Basic is granted: list accessible customers on the live account and run a read-only performance report. Then cut release 0.1.0 in CHANGELOG.md with the deployment reference.
6. Later: keyword ideas tool (needs Basic), Performance Max support, scheduled reporting.

## Open questions for the owner
- Budget cap: default is 500/day in account currency. Raise it in `/opt/ads-mcp/.env` when a campaign needs more.
- Should other Google users be able to connect? Any Google account that can see the Ads account can sign in; restrict at the Google Ads user level.

## Found undocumented
None.

## Environment and access notes
- Production URL: https://ads-mcp.mlabs.ae (MCP endpoint `/mcp`, OAuth callback `/auth/callback`).
- DigitalOcean team "Marvento Labs". Droplet `ads-mcp`, tags `ads-mcp`/`marvento`, fra1, s-1vcpu-1gb, Ubuntu 24.04. Reserved IP 159.89.212.185 (the droplet's own IP changes on every recreate; DNS points at the reserved IP only).
- Shell access: no SSH path from Cowork cloud sessions. The droplet carries one SSH key, `ads-mcp-bootstrap`, whose private half lived only in the Cowork session that created it; treat it as unusable and delete it from the DO account once a personal key is added. Day-to-day admin is the DigitalOcean web console (Droplet -> Access -> Launch Droplet Console), which logs in as root through the DO agent.
- JWT signing key: generated in the creating session and passed to every (re)create so connector logins survive rebuilds. It is not stored anywhere else yet; recreating without it just means signing in to the connector again once.
- Secrets: `/opt/ads-mcp/.env` on the droplet only. Names in `deploy/.env.example`.
- Google Cloud project: `marvento-labs-ads` (planned id; confirm in console). OAuth client "Marvento Ads MCP".
- DNS: Cloudflare zone mlabs.ae.
- Repo: github.com/MarventoCapital/marvento-ads-mcp (derived from googleads/google-ads-mcp @ d7f5e8f; published without upstream git history).
