# The editor (MLT + FFmpeg + the MCP server) on Ubuntu 24.04. Build from this folder:  docker build -t mlt-editor .
# The MLT Python binding only exists for the system Python 3.12, so the venv sees the system packages (as setup.sh does).
# Music/SFX are NOT baked in (they are not in the repository): mount a folder at /app/assets_cache or pass R2_WORKER_URL / R2_UPLOAD_TOKEN at run time.

# Optional extra CA certificates for the build (a corporate proxy): docker build --build-context ca=/path/with/ca-bundle.crt ...   (empty by default)
FROM scratch AS ca

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN --mount=type=bind,from=ca,target=/ca \
    set -e; APT=""; \
    if [ -f /ca/ca-bundle.crt ]; then \
        sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources; APT="-o Acquire::https::CaInfo=/ca/ca-bundle.crt"; \
        mkdir -p /usr/local/share/ca-certificates && cp /ca/ca-bundle.crt /usr/local/share/ca-certificates/extra.crt; \
    fi; \
    apt-get $APT update && apt-get $APT install -y --no-install-recommends \
        melt python3-mlt ffmpeg xvfb xauth fonts-dejavu-core fonts-liberation \
        python3 python3-venv python3-pip ca-certificates curl \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# a user that is not root; the project folders live in /data (a volume)
RUN useradd --create-home --uid 10001 mlt && mkdir -p /data /app && chown mlt /data /app
WORKDIR /app

COPY --chown=mlt requirements.txt pyproject.toml ./
RUN --mount=type=bind,from=ca,target=/ca \
    set -e; if [ -f /ca/ca-bundle.crt ]; then export PIP_CERT=/ca/ca-bundle.crt SSL_CERT_FILE=/ca/ca-bundle.crt REQUESTS_CA_BUNDLE=/ca/ca-bundle.crt; fi; \
    /usr/bin/python3.12 -m venv --system-site-packages /app/.venv && /app/.venv/bin/pip install -r requirements.txt && chown -R mlt /app/.venv

# DEV=1 also installs what the test suites need (pyflakes, hypothesis, playwright): docker build --build-arg DEV=1 ... and then `docker run ... ./ci.sh`
ARG DEV=0
RUN --mount=type=bind,from=ca,target=/ca \
    set -e; if [ "$DEV" = "1" ]; then \
        if [ -f /ca/ca-bundle.crt ]; then export PIP_CERT=/ca/ca-bundle.crt SSL_CERT_FILE=/ca/ca-bundle.crt REQUESTS_CA_BUNDLE=/ca/ca-bundle.crt; fi; \
        /app/.venv/bin/pip install pyflakes "hypothesis>=6.100" "playwright>=1.40"; \
    fi
COPY --chown=mlt . /app
USER mlt
# /data is the volume with every project. MLT_APP_TOKEN (the access token) is required at run time; ANTHROPIC_API_KEY is what the chat uses.
ENV MLT_APP_DATA=/data MLT_APP_HOST=0.0.0.0 MLT_APP_PORT=8080 PATH=/app/.venv/bin:$PATH
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -fsS "http://127.0.0.1:${MLT_APP_PORT}/health" || exit 1
# The web application. The MCP server alone (stdio, for Claude Code or Claude Desktop):  docker run -i -e MLT_EDITOR_HOME=/data/project -v mlt-data:/data mlt-editor python server.py
CMD ["python", "-m", "app.main"]
