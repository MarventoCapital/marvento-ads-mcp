#!/usr/bin/env python3
"""Provision the Marvento Ads MCP droplet on DigitalOcean, end to end.

Stdlib only. Reads secrets from environment variables, never from arguments,
so nothing lands in shell history.

Required env:
  DO_TOKEN                        DigitalOcean API token (write)
  GOOGLE_ADS_MCP_OAUTH_CLIENT_ID  Google OAuth client id
  GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET
Optional env:
  ADS_MCP_HOSTNAME       default ads-mcp.mlabs.ae
  ADS_MCP_ACME_EMAIL     default admin@mlabs.ae
  GOOGLE_PROJECT_ID      default marvento-labs-ads
  GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_LOGIN_CUSTOMER_ID   default empty
  ADS_MCP_MAX_DAILY_BUDGET   default 500
  GOOGLE_ADS_MCP_JWT_SIGNING_KEY   generated when absent (printed once)
  GIT_REPO               default https://github.com/MarventoCapital/marvento-ads-mcp.git
  GIT_BRANCH             default main
  DO_REGION              default fra1
  DO_SIZE                default s-1vcpu-1gb
  DO_IMAGE               default ubuntu-24-04-x64
  DROPLET_NAME           default ads-mcp
  CF_TOKEN               Cloudflare token (Zone:DNS:Edit) to create the A record
  CF_ZONE                default derived from hostname (last two labels)

Usage:
  python3 deploy/create_droplet.py            # create everything
  python3 deploy/create_droplet.py --dns-only # only (re)point DNS at the reserved IP
  python3 deploy/create_droplet.py --status   # show droplet + reserved IP
  python3 deploy/create_droplet.py --recreate # destroy + recreate (e.g. new secrets);
                                              # keeps the reserved IP, so DNS stays valid.
                                              # Pass the same GOOGLE_ADS_MCP_JWT_SIGNING_KEY
                                              # to keep existing connector logins.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DO_API = "https://api.digitalocean.com/v2"
CF_API = "https://api.cloudflare.com/client/v4"


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"missing required env var {name}")
    return value or ""


class HttpError(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(f"HTTP {code}: {detail}")
        self.code = code
        self.detail = detail


def http(
    method: str, url: str, token: str, body: dict | None = None, fatal: bool = True
) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if fatal:
            sys.exit(f"{method} {url} -> HTTP {e.code}: {detail}")
        raise HttpError(e.code, detail)


# --------------------------------------------------------------------------- #
# DigitalOcean
# --------------------------------------------------------------------------- #


def do_find_droplet(token: str, name: str) -> dict | None:
    page = http("GET", f"{DO_API}/droplets?tag_name=ads-mcp&per_page=200", token)
    for d in page.get("droplets", []):
        if d["name"] == name:
            return d
    return None


def do_find_reserved_ip(token: str, region: str) -> str | None:
    page = http("GET", f"{DO_API}/reserved_ips?per_page=200", token)
    for ip in page.get("reserved_ips", []):
        if ip.get("region", {}).get("slug") == region and not ip.get("droplet"):
            return ip["ip"]
    return None


def do_reserved_ip_of(token: str, droplet_id: int) -> str | None:
    page = http("GET", f"{DO_API}/reserved_ips?per_page=200", token)
    for ip in page.get("reserved_ips", []):
        d = ip.get("droplet")
        if d and d.get("id") == droplet_id:
            return ip["ip"]
    return None


def do_ssh_key_ids(token: str) -> list[int]:
    page = http("GET", f"{DO_API}/account/keys?per_page=200", token)
    return [k["id"] for k in page.get("ssh_keys", [])]


def destroy_keep_ip(token: str, droplet: dict) -> None:
    """Destroys a droplet and waits until it is gone. Its reserved IP becomes
    unassigned (it is not released), so DNS keeps pointing at the same address."""
    did = droplet["id"]
    print(f"destroying droplet {droplet['name']} (id {did}); the reserved IP is kept")
    http("DELETE", f"{DO_API}/droplets/{did}", token)
    for _ in range(60):
        try:
            http("GET", f"{DO_API}/droplets/{did}", token, fatal=False)
        except HttpError as e:
            if e.code == 404:
                break
            raise
        time.sleep(5)
    else:
        sys.exit("droplet was not destroyed within 5 minutes")
    # The reserved IP is released from the droplet asynchronously.
    for _ in range(24):
        page = http("GET", f"{DO_API}/reserved_ips?per_page=200", token)
        if not any((ip.get("droplet") or {}).get("id") == did for ip in page.get("reserved_ips", [])):
            return
        time.sleep(5)


def resolves_to(hostname: str, ip: str) -> bool:
    import socket
    try:
        return ip in {a[4][0] for a in socket.getaddrinfo(hostname, 443, socket.AF_INET)}
    except OSError:
        return False


def render_user_data(values: dict) -> str:
    template = (Path(__file__).parent / "cloud-init.yaml").read_text()
    for key, val in values.items():
        template = template.replace("{{" + key + "}}", str(val))
    leftover = [w for w in template.split() if w.startswith("{{")]
    if leftover:
        sys.exit(f"unfilled placeholders in cloud-init: {leftover}")
    return template


def create(token: str, recreate: bool = False) -> None:
    name = env("DROPLET_NAME", "ads-mcp")
    region = env("DO_REGION", "fra1")
    hostname = env("ADS_MCP_HOSTNAME", "ads-mcp.mlabs.ae")

    existing = do_find_droplet(token, name)
    if existing and not recreate:
        sys.exit(
            f"droplet '{name}' already exists; use --recreate to replace it "
            "(the reserved IP and DNS stay), or --status"
        )
    if existing:
        destroy_keep_ip(token, existing)

    jwt_key = env("GOOGLE_ADS_MCP_JWT_SIGNING_KEY")
    generated = False
    if not jwt_key:
        jwt_key = secrets.token_urlsafe(48)
        generated = True

    values = {
        "oauth_client_id": env("GOOGLE_ADS_MCP_OAUTH_CLIENT_ID", required=True),
        "oauth_client_secret": env("GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET", required=True),
        "hostname": hostname,
        "jwt_signing_key": jwt_key,
        "google_project_id": env("GOOGLE_PROJECT_ID", "marvento-labs-ads"),
        "developer_token": env("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
        "login_customer_id": env("GOOGLE_ADS_LOGIN_CUSTOMER_ID", ""),
        "max_daily_budget": env("ADS_MCP_MAX_DAILY_BUDGET", "500"),
        "acme_email": env("ADS_MCP_ACME_EMAIL", "admin@mlabs.ae"),
        "git_repo": env("GIT_REPO", "https://github.com/MarventoCapital/marvento-ads-mcp.git"),
        "git_branch": env("GIT_BRANCH", "main"),
    }
    user_data = render_user_data(values)

    reserved_ip = do_find_reserved_ip(token, region)
    if reserved_ip:
        print(f"reusing unassigned reserved IP {reserved_ip}")
    else:
        resp = http("POST", f"{DO_API}/reserved_ips", token, {"region": region})
        reserved_ip = resp["reserved_ip"]["ip"]
        print(f"reserved IP {reserved_ip} in {region}")

    body = {
        "name": name,
        "region": region,
        "size": env("DO_SIZE", "s-1vcpu-1gb"),
        "image": env("DO_IMAGE", "ubuntu-24-04-x64"),
        "ssh_keys": do_ssh_key_ids(token),
        "backups": False,
        "ipv6": True,
        "monitoring": True,
        "tags": ["ads-mcp", "marvento"],
        "user_data": user_data,
    }
    resp = http("POST", f"{DO_API}/droplets", token, body)
    droplet_id = resp["droplet"]["id"]
    print(f"droplet {name} created, id {droplet_id}; waiting for it to boot")

    for _ in range(60):
        d = http("GET", f"{DO_API}/droplets/{droplet_id}", token)["droplet"]
        if d["status"] == "active":
            break
        time.sleep(5)
    else:
        sys.exit("droplet did not become active in 5 minutes")

    # DO rejects the assignment (422) while the droplet still has its create
    # event pending, so retry for a couple of minutes.
    for attempt in range(24):
        try:
            http(
                "POST",
                f"{DO_API}/reserved_ips/{reserved_ip}/actions",
                token,
                {"type": "assign", "droplet_id": droplet_id},
                fatal=False,
            )
            break
        except HttpError as e:
            if e.code not in (409, 422) or attempt == 23:
                sys.exit(f"assigning reserved IP failed: {e}")
            time.sleep(5)
    print(f"assigned reserved IP {reserved_ip} to droplet {droplet_id}")

    if generated:
        print(
            "\nGenerated GOOGLE_ADS_MCP_JWT_SIGNING_KEY (stored only in /opt/ads-mcp/.env on the droplet).\n"
            "Keep a copy if you ever rebuild the box and want existing connector logins to survive:\n"
            f"  {jwt_key}\n"
        )

    cf_token = env("CF_TOKEN")
    if cf_token:
        cloudflare_upsert_a(cf_token, hostname, reserved_ip)
    elif resolves_to(hostname, reserved_ip):
        print(f"\nDNS: {hostname} already resolves to {reserved_ip}.")
    else:
        print(
            f"\nDNS: create an A record  {hostname} -> {reserved_ip}  (DNS only, not proxied).\n"
            "Create it before the droplet finishes booting: Caddy requests the certificate\n"
            "on first start, and early failures trip Let's Encrypt's failed-validation limit."
        )

    print(
        "\nCloud-init now installs Docker, builds the image and starts Caddy.\n"
        "First boot takes 3 to 6 minutes. Then check:\n"
        f"  curl -s https://{hostname}/.well-known/oauth-authorization-server | head -c 200\n"
        f"MCP endpoint for claude.ai custom connector:  https://{hostname}/mcp"
    )


def status(token: str) -> None:
    name = env("DROPLET_NAME", "ads-mcp")
    d = do_find_droplet(token, name)
    if not d:
        print("no droplet named", name)
        return
    v4 = [n["ip_address"] for n in d["networks"]["v4"] if n["type"] == "public"]
    print(json.dumps({
        "id": d["id"], "status": d["status"], "region": d["region"]["slug"],
        "size": d["size_slug"], "public_ipv4": v4,
        "reserved_ip": do_reserved_ip_of(token, d["id"]),
        "created_at": d["created_at"],
    }, indent=2))


# --------------------------------------------------------------------------- #
# Cloudflare
# --------------------------------------------------------------------------- #


def cloudflare_upsert_a(cf_token: str, hostname: str, ip: str) -> None:
    zone_name = env("CF_ZONE") or ".".join(hostname.split(".")[-2:])
    zones = http("GET", f"{CF_API}/zones?name={zone_name}", cf_token)
    if not zones.get("result"):
        sys.exit(f"Cloudflare zone {zone_name} not visible to this token")
    zone_id = zones["result"][0]["id"]
    existing = http(
        "GET", f"{CF_API}/zones/{zone_id}/dns_records?type=A&name={hostname}", cf_token
    ).get("result", [])
    record = {"type": "A", "name": hostname, "content": ip, "ttl": 300, "proxied": False}
    if existing:
        http("PUT", f"{CF_API}/zones/{zone_id}/dns_records/{existing[0]['id']}", cf_token, record)
        print(f"Cloudflare: updated A {hostname} -> {ip} (DNS only)")
    else:
        http("POST", f"{CF_API}/zones/{zone_id}/dns_records", cf_token, record)
        print(f"Cloudflare: created A {hostname} -> {ip} (DNS only)")


def main() -> None:
    token = env("DO_TOKEN", required=True)
    if "--status" in sys.argv:
        status(token)
    elif "--dns-only" in sys.argv:
        d = do_find_droplet(token, env("DROPLET_NAME", "ads-mcp"))
        if not d:
            sys.exit("no droplet to point DNS at")
        ip = do_reserved_ip_of(token, d["id"])
        cloudflare_upsert_a(env("CF_TOKEN", required=True), env("ADS_MCP_HOSTNAME", "ads-mcp.mlabs.ae"), ip)
    else:
        create(token, recreate="--recreate" in sys.argv)


if __name__ == "__main__":
    main()
