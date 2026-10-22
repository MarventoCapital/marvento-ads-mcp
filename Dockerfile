# Pull a fresh base image on every security rebuild.
FROM python:3.11-slim

# Pin uv to a release that excludes the compromised emSign CA.
# Never rely on the floating "latest" tag for TLS bootstrap.
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/

# Refresh OS certificate roots. An OS update alone is NOT sufficient for pip.
RUN apt-get update \
    && apt-get install --no-install-recommends --only-upgrade -y ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the project files into the container
COPY . .

# Use already-patched uv to bootstrap pip and the application. This avoids
# downloading the first updated package through an old vulnerable pip.
RUN uv pip install --system --upgrade "pip>=26.2.2" "certifi>=2026.10.19" .

# Fail closed if the image carries the affected installers or CA bundle.
RUN python -c "from importlib.metadata import version; from pathlib import Path; import certifi; v=lambda n: tuple(map(int, version(n).split('.'))); assert v('pip') >= (26, 2, 2), version('pip'); assert v('certifi') >= (2026, 10, 19), version('certifi'); assert 'emSign ECC Root CA - G3' not in Path(certifi.where()).read_text(), 'Compromised root still trusted'" \
    && python -m pip check \
    && uv --version

# Codex CLI 0.146 reports the RFC 9207 `iss` value as missing from the local
# OAuth callback, even though FastMCP constructs the redirect with `iss` and
# advertises it as mandatory. This version-guarded compatibility layer only
# stops affected clients from requiring that response parameter.
RUN python -c "from importlib.metadata import version; from pathlib import Path; assert version('fastmcp') == '4.0.3'; p = Path('/usr/local/lib/python3.11/site-packages/fastmcp/server/auth/oauth_proxy/proxy.py'); s = p.read_text(); old = 'metadata.authorization_response_iss_parameter_supported = True'; new = 'metadata.authorization_response_iss_parameter_supported = False'; assert s.count(old) == 1; p.write_text(s.replace(old, new))"

LABEL io.google-mcp.workaround="openai-codex-0146-rfc9207-iss"

# Expose port 8080 (default for Cloud Run)
EXPOSE 8080

# Define the command to run the server
# This uses the entry point defined in pyproject.toml
CMD ["google-ads-mcp"]
