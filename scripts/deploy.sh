#!/usr/bin/env bash
# scripts/deploy.sh — run on the VM (over SSH from up.sh) to install OneAgent,
# Node 20, Nginx, clone the app, and start the systemd service.
#
# Idempotent: safe to re-run on an existing VM after pushing new code.
# Required env vars (passed via `sudo DT_URL=... DT_TOKEN=... bash -s`):
#   DT_URL   — Dynatrace OneAgent env URL, e.g. https://<env>.sprint.dynatracelabs.com
#   DT_TOKEN — Dynatrace PaaS token, scope InstallerDownload
# Optional:
#   GIT_REPO — defaults to https://github.com/SudoSmitty/fieldops-demo.git
#   GIT_REF  — defaults to main

set -euo pipefail

: "${DT_URL:?DT_URL not set}"
: "${DT_TOKEN:?DT_TOKEN not set}"
GIT_REPO="${GIT_REPO:-https://github.com/SudoSmitty/fieldops-demo.git}"
GIT_REF="${GIT_REF:-main}"

echo "== wait for cloud-init (best effort) =="
cloud-init status --wait 2>/dev/null || true

echo "== install base packages =="
apt-get update -qq
apt-get install -y nginx git curl wget ca-certificates

echo "== install Node 20 =="
if ! command -v node >/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
node --version

echo "== install Dynatrace OneAgent (idempotent) =="
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
cd /opt/fieldops/backend && npm install --omit=dev --no-audit --no-fund

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
