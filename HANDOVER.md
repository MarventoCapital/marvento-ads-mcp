# Handover: Marvento Ads MCP

Rules: see AGENTS.md.
Last updated: 2026-09-27 20:25 Asia/Dubai by Luis via Claude.
Current version: unreleased; production droplet live with the real Google OAuth client. Baseline upstream d7f5e8f.

## Current state
Code on GitHub (MarventoCapital/marvento-ads-mcp, public, main); 100 tests pass on a clean install. Server live at https://ads-mcp.mlabs.ae on droplet 604125096 since 2026-09-27 16:17 UTC, using the real Google OAuth client from Cloud project `marvento-labs-ads`. Verified up to Google's account chooser (Google accepts the client and redirect URI). Not yet done: a real sign-in through the claude.ai connector and the first tool calls.

## Work in progress
- Connector (Luis): add `https://ads-mcp.mlabs.ae/mcp` as a custom connector in claude.ai and sign in with Google. Expect a one-time "Google hasn't verified this app" warning (Advanced -> continue), because the app requests the sensitive `adwords` scope and is not verified yet.
- Google side (Luis): checklist steps 7-10 remain: brand verification (needs Search Console ownership of mlabs.ae), Explorer/Basic access application on the Google Ads API "Access levels" page, note the Marvento Labs customer ID, test manager account.

## Blockers
- Google API access level: the new project starts at Test access, so calls against the live Marvento Labs account fail with `Cloud_Project_Not_Approved_For_Production` until Explorer or Basic is granted. Test accounts work meanwhile.
- Resolved 2026-09-27: org egress allowlist for api.digitalocean.com, api.cloudflare.com, ads-mcp.mlabs.ae.
- Resolved 2026-09-27: real Google OAuth client created and deployed (was placeholder).

## Next steps (priority order)
1. Luis adds the connector in claude.ai and signs in with Google; confirm 23 tools are listed.
2. Smoke test on a Google Ads **test** account: `customers_list_accessible_customers`, then budget -> campaign -> targeting -> ad group -> keywords -> RSA -> sitelinks -> `set_campaign_status ENABLED confirm=true`, and read it all back with `search_search`.
3. Apply for Explorer/Basic access (Cloud console -> APIs & Services -> Google Ads API -> Access levels -> Manage). Basic needs brand verification first (Google Auth Platform -> Verification Center).
4. When granted: list accessible customers on the live account and run a read-only performance report. Then cut release 0.1.0 in CHANGELOG.md with the deployment reference.
5. Luis revokes the DigitalOcean API token that was shared in chat (DO -> API -> Tokens). A new one is only needed for the next `--recreate`.
6. Later: keyword ideas tool (needs Basic), Performance Max support, scheduled reporting.

## Open questions for the owner
- Budget cap: default is 500/day in account currency. Raise it in `/opt/ads-mcp/.env` when a campaign needs more.
- Should other Google users be able to connect? Any Google account that can see the Ads account can sign in; restrict at the Google Ads user level. The unverified app allows at most 100 users over its lifetime.

## Found undocumented
None.

## Environment and access notes
- Production URL: https://ads-mcp.mlabs.ae (MCP endpoint `/mcp`, OAuth callback `/auth/callback`).
- DigitalOcean team "Marvento Labs". Droplet `ads-mcp` id 604125096, tags `ads-mcp`/`marvento`, fra1, s-1vcpu-1gb, Ubuntu 24.04. Reserved IP 159.89.212.185 (the droplet's own IP changes on every recreate; DNS points at the reserved IP only).
- DNS: Cloudflare zone mlabs.ae, A record `ads-mcp` -> 159.89.212.185, DNS only.
- Google Cloud project `marvento-labs-ads` ("Marvento Labs Ads"), owned by Luis's Google account. Google Ads API enabled. Google Auth Platform: app "Marvento Ads MCP", External, In production (unverified), home/privacy/terms on mlabs.ae, authorized domain mlabs.ae, scopes openid + userinfo.email + userinfo.profile + adwords. OAuth client "Marvento Ads MCP" (Web application), redirect URI `https://ads-mcp.mlabs.ae/auth/callback`.
- OAuth client secret: shown by Google only once at creation. It lives in `/opt/ads-mcp/.env` on the droplet (`GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET`). If it is lost, add a new secret to the same client in the Cloud console and `--recreate`.
- Shell access: no SSH path from Cowork cloud sessions. The droplet carries one SSH key, `ads-mcp-bootstrap`, whose private half lived only in the Cowork session that created it; treat it as unusable and delete it from the DO account once a personal key is added. Day-to-day admin is the DigitalOcean web console (Droplet -> Access -> Launch Droplet Console), which logs in as root through the DO agent.
- JWT signing key: generated in the creating session and passed to every (re)create so connector logins survive rebuilds. It is not stored anywhere else except `/opt/ads-mcp/.env`; recreating without it just means signing in to the connector again once.
- Secrets: `/opt/ads-mcp/.env` on the droplet only. Names in `deploy/.env.example`.
- Repo: github.com/MarventoCapital/marvento-ads-mcp (derived from googleads/google-ads-mcp @ d7f5e8f; published without upstream git history).
