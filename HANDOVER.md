# Handover: Marvento Ads MCP

Rules: see AGENTS.md.
Last updated: 2026-09-27 15:40 Asia/Dubai by Luis via Claude.
Current version: unreleased; test deployment live on the droplet (placeholder OAuth). Baseline upstream d7f5e8f.

## Current state
Code on GitHub (MarventoCapital/marvento-ads-mcp, public, main); 100 tests pass on a clean install. Server live at https://ads-mcp.mlabs.ae on droplet 604080130 since 2026-09-27 11:32 UTC with PLACEHOLDER Google OAuth values. Verified end to end up to the Google sign-in redirect (certificate, 401 challenge, client registration, consent, redirect to Google with `https://ads-mcp.mlabs.ae/auth/callback`). Google sign-in and tool calls wait on the real OAuth client.

## Work in progress
- Deployment (Claude): droplet `ads-mcp` id 604080130 on reserved IP 159.89.212.185 runs placeholder OAuth values. Next action is `--recreate` with the real OAuth client ID/secret. Cloudflare A record `ads-mcp.mlabs.ae -> 159.89.212.185` (DNS only, TTL Auto) exists since ~10:43 UTC; added by Claude through Luis's Cloudflare dashboard session.
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
