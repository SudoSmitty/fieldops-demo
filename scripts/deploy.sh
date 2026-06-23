#!/usr/bin/env bash
# scripts/deploy.sh — run on the VM (over SSH from up.sh) to install OneAgent,
# Python 3.11, Nginx, clone the app, and start the systemd service.
#
# Idempotent: safe to re-run on an existing VM after pushing new code.
# Required env vars (passed via `sudo DT_URL=... DT_TOKEN=... bash -s`):
#   DT_URL   — Dynatrace OneAgent env URL (host monitoring tenant)
#   DT_TOKEN — Dynatrace PaaS token, scope InstallerDownload
# Optional:
#   DT_OTLP_ENDPOINT — Dynatrace OTLP base URL (e.g. https://<env>.live.dynatrace.com/api/v2/otlp)
#                      Traceloop ships gen_ai.* spans here. If unset, traces stay local (no-op exporter).
#   DT_API_TOKEN     — API token with `openTelemetryTrace.ingest` scope on the OTLP tenant.
#   GIT_REPO — defaults to https://github.com/SudoSmitty/fieldops-demo.git
#   GIT_REF  — defaults to main

set -euo pipefail

: "${DT_URL:?DT_URL not set}"
: "${DT_TOKEN:?DT_TOKEN not set}"
GIT_REPO="${GIT_REPO:-https://github.com/SudoSmitty/fieldops-demo.git}"
GIT_REF="${GIT_REF:-main}"
DT_OTLP_ENDPOINT="${DT_OTLP_ENDPOINT:-}"
DT_API_TOKEN="${DT_API_TOKEN:-}"

echo "== wait for cloud-init (best effort) =="
cloud-init status --wait 2>/dev/null || true

echo "== install base packages =="
apt-get update -qq
apt-get install -y nginx git curl wget ca-certificates \
                   python3.11 python3.11-venv python3-pip

echo "== install Dynatrace OneAgent (idempotent, unchanged) =="
wget -qO /tmp/oneagent.sh \
  "${DT_URL}/api/v1/deployment/installer/agent/unix/default/latest?arch=x86&flavor=default" \
  --header="Authorization: Api-Token ${DT_TOKEN}"
sh /tmp/oneagent.sh --set-app-log-content-access=true --set-host-group=fieldops-demo

echo "== deploy app from $GIT_REPO ($GIT_REF) =="
if [ -d /opt/fieldops/.git ]; then
  git -C /opt/fieldops fetch --quiet origin
  git -C /opt/fieldops reset --hard "origin/$GIT_REF" --quiet
else
  rm -rf /opt/fieldops
  git clone --branch "$GIT_REF" --depth 1 "$GIT_REPO" /opt/fieldops
fi

echo "== Python venv + deps =="
cd /opt/fieldops/backend
python3.11 -m venv .venv
./.venv/bin/pip install --upgrade --quiet pip
./.venv/bin/pip install --quiet -r requirements.txt

echo "== write /etc/fieldops/backend.env (systemd EnvironmentFile) =="
mkdir -p /etc/fieldops
cat > /etc/fieldops/backend.env <<EOF
AGENT_MODE=mock
OTEL_SERVICE_NAME=fieldops-backend
DT_OTLP_ENDPOINT=${DT_OTLP_ENDPOINT}
DT_API_TOKEN=${DT_API_TOKEN}
EOF
chmod 600 /etc/fieldops/backend.env

echo "== install systemd unit + restart =="
cp /opt/fieldops/backend/fieldops-backend.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fieldops-backend
systemctl restart fieldops-backend

echo "== publish frontend =="
cp /opt/fieldops/frontend/index.html /var/www/html/index.html

echo "== configure nginx (SSE-safe proxy) =="
cat > /etc/nginx/sites-available/default <<'NGINX'
server {
  listen 80 default_server;
  root /var/www/html; index index.html;
  location / { try_files $uri /index.html; }
  location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 300s;
  }
}
NGINX
nginx -t
systemctl restart nginx

echo "== verify =="
sleep 2
systemctl is-active fieldops-backend nginx oneagent
echo "DONE"
