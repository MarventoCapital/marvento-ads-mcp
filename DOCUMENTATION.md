# Documentation: Marvento Ads MCP

## Purpose
A remote MCP server that lets Claude (claude.ai, Cowork, Claude Code) read and manage the Marvento Labs Google Ads account. "Working" means: Luis adds the server as a custom connector in claude.ai, logs in with his Google account once, and can then report on, build and adjust campaigns from any device, with guard rails that prevent an agent from starting or scaling spend without an explicit confirmation.

## How it works
Fork of Google's official `google-ads-mcp` (Python, FastMCP 4, google-ads client library, API v25).

Request path: claude.ai -> `https://ads-mcp.mlabs.ae/mcp` (Caddy, TLS) -> `mcp` container on :8080 (streamable HTTP) -> Google Ads API over gRPC.

Auth: FastMCP `GoogleProvider` (OAuth proxy). The server is itself an OAuth authorization server for MCP clients (dynamic client registration at `/register`) and uses a Google OAuth *web* client upstream. When a client connects, the user signs in with Google and grants the `adwords` scope; the server stores the Google refresh token (encrypted, filetree store at `/data/oauth` in the `oauth_store` volume) and issues its own JWT to the MCP client. Every tool call then runs the Google Ads API with the signed-in user's own Google credentials, so account access equals whatever that Google user can see in Google Ads.

Tools are auto-discovered: every module in `ads_mcp/tools/` that defines a `FastMCP(...)` sub-server is mounted with its name as prefix (e.g. `campaigns_create_campaign`). `ads_mcp/tools_config.yaml` turns namespaces on and off.

Stack and platforms: DigitalOcean droplet `ads-mcp` (fra1, s-1vcpu-1gb, Ubuntu 24.04) provisioned by cloud-init; Docker Compose; Caddy 2 for HTTPS (Let's Encrypt); Cloudflare DNS for `mlabs.ae` (record must be DNS-only, not proxied, so SSE streams are not buffered and certificates issue directly); Google Cloud project `marvento-labs-ads` (created 2026-09-27; Google Ads API enabled; OAuth web client "Marvento Ads MCP"; Ads API access level, Test until approved).

Environments: local dev runs the server with `google-ads-mcp` (stdio) or, with the OAuth env vars set, on `http://localhost:8080/mcp`. Production is the droplet. Env vars live in `/opt/ads-mcp/.env` on the droplet (names in `deploy/.env.example`); nothing secret is in the repo.

## Key areas
- `ads_mcp/coordinator.py` (upstream): builds the FastMCP app, wires `GoogleProvider` when `GOOGLE_ADS_MCP_OAUTH_CLIENT_ID/SECRET` are set, mounts tool namespaces.
- `ads_mcp/utils.py` (upstream): builds a `GoogleAdsClient` per call from the FastMCP access token (or ADC locally); `login_customer_id` handling.
- `ads_mcp/mutate.py` (fork): shared mutate execution, `confirm_gate`, budget cap, micros conversion, resource-name builders, GAQL helper, language-code resolution, error formatting.
- `ads_mcp/tools/campaigns.py` (fork): budgets, Search campaigns (bidding: MAXIMIZE_CLICKS default, MAXIMIZE_CONVERSIONS, MAXIMIZE_CONVERSION_VALUE, MANUAL_CPC), status, dates, geo suggestion, locations/languages/negatives.
- `ads_mcp/tools/ad_groups.py` (fork): ad groups, keywords, negatives, keyword status, default CPC.
- `ads_mcp/tools/ads.py` (fork): responsive search ads with local copy validation (3-15 headlines <=30 chars, 2-4 descriptions <=90, paths <=15, pins), update, status.
- `ads_mcp/tools/assets.py` (fork): sitelinks, callouts (asset + campaign link, two calls), website conversion actions.
- `deploy/` (fork): see "How to deploy".

## Google access levels (state on 2026-09-27)
Developer tokens were sunset on 2026-09-09; the access level now belongs to the Google Cloud project that owns the OAuth client.
- Test: default for a new project; test accounts only. Production calls fail with `Cloud_Project_Not_Approved_For_Production`.
- Explorer: production allowed, 2,880 ops/day, no planning services (Keyword Planner), no account creation or billing. Applied for after sign-up; Google may auto-upgrade.
- Basic: 15,000 ops/day, needs OAuth brand verification of the project first, then an automated application reviewed in minutes.
- Standard: unlimited, manual audit, about 10 business days.
Where: Google Cloud Console -> APIs & Services -> Google Ads API -> Overview / API access page. Brand verification: Google Auth Platform -> Verification Center (needs Search Console ownership of mlabs.ae).

## How to deploy
First deployment (from any machine with Python 3, no SSH needed):
1. `export DO_TOKEN=... GOOGLE_ADS_MCP_OAUTH_CLIENT_ID=... GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET=...` (plus `CF_TOKEN` to have the A record created).
2. `python3 deploy/create_droplet.py` — reserves an IP, creates the droplet with cloud-init, assigns the IP, prints the JWT signing key once.
3. Wait 3 to 6 minutes; verify `curl -s https://ads-mcp.mlabs.ae/.well-known/oauth-authorization-server`.
4. In claude.ai: Settings -> Connectors -> Add custom connector -> URL `https://ads-mcp.mlabs.ae/mcp`, then Connect and sign in with Google.

Redeploy after a code change: merge to `main`, then on the droplet `sudo /opt/ads-mcp/src/deploy/update.sh` (DO web console or SSH with a key registered in the DO account at creation time). The script pulls, rebuilds, restarts and waits for the health check.

Verify: the well-known endpoint returns 200; `/mcp` returns 401 with a `WWW-Authenticate` header when unauthenticated; in claude.ai the connector lists 23 tools; `customers_list_accessible_customers` returns ids.

Roll back: `cd /opt/ads-mcp/src && git checkout <previous-commit> && cd deploy && docker compose up -d --build`. The OAuth store volume is independent of the image, so connector logins survive a rollback.

Rebuild from scratch: destroy the droplet in DO, keep the reserved IP, run `create_droplet.py` again with the same `GOOGLE_ADS_MCP_JWT_SIGNING_KEY` if existing logins should keep working (otherwise users reconnect once).

## Decisions

### 2026-09-27: Fork Google's official server instead of writing a new MCP server
Context: needed read + write Google Ads tools reachable remotely with proper OAuth.
Decision: fork `googleads/google-ads-mcp`, add write namespaces, keep upstream code untouched.
Why: upstream already solves the hard parts (OAuth proxy with Google, streamable HTTP, GAQL search with resource hints, Cloud Run-grade Dockerfile, tests). Adding tools is additive and upstream changes can be merged.
Rejected: hosted SaaS MCPs (third party holds account access, monthly fee, gated writes); from-scratch FastMCP server (re-implements the OAuth proxy for no gain); n8n Google Ads node (read-only, weak).
Consequences: Python/FastMCP stack; must track FastMCP 4.0.3 pin; write tools must follow upstream's mounting conventions.

### 2026-09-27: Own VPS on DigitalOcean rather than Cloud Run or a laptop
Context: Luis wants to run ads "from anywhere" through Claude; the server must be a remote connector.
Decision: $6 droplet, Docker Compose, Caddy auto-HTTPS, cloud-init provisioning, reserved IP.
Why: fully under Luis's control, one place to add more MCP servers later, no vendor lock; cloud-init means zero manual server work.
Rejected: Cloud Run (fine technically, but keeps infra inside Google and needs gcloud tooling); local stdio (not reachable from phone/other machines).
Consequences: a box to patch (unattended-upgrades on, auto-reboot 04:30 Dubai); single instance, filetree token store (no Redis needed).

### 2026-09-27: Safety rails live in the server, not in prompts
Decision: create PAUSED, `confirm` gate, budget cap in env, `validate_only` everywhere, local copy validation.
Why: a prompt can be forgotten; a server rule cannot. The cap is deliberately not a tool parameter so the model cannot lift it.
Consequences: enabling a campaign or raising a budget is always a two-step conversation; raising the cap means editing `/opt/ads-mcp/.env` and restarting.

### 2026-09-27: Hostname ads-mcp.mlabs.ae, Cloudflare DNS-only
Why: the OAuth redirect URI, authorized domain and brand verification all hang off a domain Luis owns; mlabs.ae is Marvento Labs. DNS-only because Cloudflare's proxy buffers SSE and complicates ACME.

## Change history
- 2026-09-27 (unreleased): write tools, safety rails, deployment kit, docs. See CHANGELOG.md.

## Known issues and gotchas
- Google OAuth app in "Testing" publishing status expires refresh tokens after 7 days. The app must be published to production (unverified is fine for one user) or logins break weekly. Done 2026-09-27: the app is In production, unverified.
- Unverified production app: sign-in shows a one-time "Google hasn't verified this app" warning because `adwords` is a sensitive scope, and at most 100 users can ever grant it. Brand verification (Verification Center) removes both.
- New Google OAuth client secrets are shown only once, in the "OAuth client created" dialog. The browser automation in Cowork cannot read them (the Chrome extension blocks secret-looking values), so the owner copies it by hand. If lost, add a new secret to the same client and `--recreate`.
- Campaign dates use `start_date_time` / `end_date_time` in API v25 (`YYYY-MM-DD HH:MM:SS`); the tools accept `YYYY-MM-DD` and expand.
- `contains_eu_political_advertising` is a required declaration on campaign creation; the tools default it to "does not contain".
- Explorer access blocks planning services, so a keyword-ideas tool would fail until Basic is granted.
- Test accounts: writes can be exercised end to end under Test access using a test manager account; metrics there are always zero.
- The Dockerfile asserts `fastmcp==4.0.3` (Codex RFC 9207 workaround); bumping FastMCP means revisiting that patch.
- Order matters on first deploy: the DNS A record must resolve before the droplet boots. Otherwise Caddy's first ACME attempts fail on a cached NXDOMAIN, Let's Encrypt's failed-validation limit (5 per hour per account and hostname) kicks in, and HTTPS stays down until Caddy's backoff retries after the limit clears. Symptom from outside: TLS handshake fails with "tlsv1 alert internal error". Fastest fix: `create_droplet.py --recreate` (new ACME account) once DNS resolves.
- `pyproject.toml` restricts package discovery to `ads_mcp*`. Any new top-level folder with `.py` files (like `deploy/`) would otherwise break `pip install .` and the Docker build with "Multiple top-level packages discovered".
- The repo on GitHub was published through the connector, so it does not carry upstream git history. To merge upstream changes: `git remote add upstream https://github.com/googleads/google-ads-mcp.git`, fetch, and cherry-pick or diff by hand.

## Testing
`python -m unittest discover -s tests -p "*_test.py"` (100 tests). Write tools are tested with a real `GoogleAdsClient` for types and a recorder in place of the service layer, so request protos and gates are checked without network. Live checks against a Google Ads test account are manual: see HANDOVER.md.
