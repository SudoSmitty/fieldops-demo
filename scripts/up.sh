#!/usr/bin/env bash
# scripts/up.sh — bring up the FieldOps demo end-to-end.
#
# Usage:
#   export DT_INFRA_URL='https://<env>.<seg>.dynatrace.com'   # OneAgent install tenant
#   export DT_OTLP_ENDPOINT='https://<env>.<seg>.dynatrace.com/api/v2/otlp'  # AI Obs tenant
#   export TF_VAR_dt_paas_token='dt0c01....'   # PaaS token on DT_INFRA_URL (InstallerDownload)
#   export DT_API_TOKEN='dt0c01....'           # API token on DT_OTLP_ENDPOINT (openTelemetryTrace.ingest)
#   ./scripts/up.sh
#
# (Anything not in env is prompted for at runtime. Tokens never land on disk.)
# Single-tenant deployments: set DT_INFRA_URL and DT_OTLP_ENDPOINT to the same tenant.
#
# What this does (idempotent — safe to re-run):
#   1. Prompt / verify tenant URLs and tokens
#   2. Verify prereqs (terraform, az, ssh-keygen, ssh, curl)
#   3. Generate ~/.ssh/fieldops_rsa if missing (azurerm rejects ed25519)
#   4. Refresh allowed_ip in terraform.tfvars to your current public IP
#   5. terraform apply  (creates RG, VNet, NSG, public IP, VM)
#   6. SSH to the VM and run scripts/deploy.sh (installs OneAgent, Python, Nginx, app)
#   7. Smoke test: curl the public URL, confirm SSE event stream

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA="$ROOT/infra"
KEY="$HOME/.ssh/fieldops_rsa"
TFVARS="$INFRA/terraform.tfvars"

banner() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
warn()   { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die()    { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# Derive the "apps" UI URL from a base tenant URL.
# Examples:
#   https://abc.live.dynatrace.com         -> https://abc.live.apps.dynatrace.com
#   https://abc.sprint.dynatracelabs.com   -> https://abc.sprint.apps.dynatracelabs.com
apps_url_of() {
  printf '%s' "$1" | sed -E 's|\.(dynatrace(labs)?\.com)$|.apps.\1|'
}

banner "1. tenant + tokens"

if [ -z "${DT_INFRA_URL:-}" ]; then
  echo "Tenant URL used for OneAgent install on the VM (host/process/RUM/logs)."
  read -p 'Dynatrace infra tenant URL (e.g. https://abc.live.dynatrace.com): ' DT_INFRA_URL
  export DT_INFRA_URL
fi
[ -n "$DT_INFRA_URL" ] || die "no DT_INFRA_URL"
DT_INFRA_URL="${DT_INFRA_URL%/}"   # strip trailing slash

if [ -z "${DT_OTLP_ENDPOINT:-}" ]; then
  DEFAULT_OTLP="${DT_INFRA_URL}/api/v2/otlp"
  echo "Tenant OTLP endpoint where the AI Observability app will surface prompts."
  echo "Use the same tenant as infra unless intentionally splitting."
  read -p "OTLP endpoint [$DEFAULT_OTLP]: " DT_OTLP_ENDPOINT
  DT_OTLP_ENDPOINT="${DT_OTLP_ENDPOINT:-$DEFAULT_OTLP}"
  export DT_OTLP_ENDPOINT
fi
[ -n "$DT_OTLP_ENDPOINT" ] || die "no DT_OTLP_ENDPOINT"

INFRA_APPS_URL="$(apps_url_of "$DT_INFRA_URL")"
OTLP_BASE="${DT_OTLP_ENDPOINT%/api/v2/otlp}"
OTLP_APPS_URL="$(apps_url_of "$OTLP_BASE")"

if [ -z "${TF_VAR_dt_paas_token:-}" ]; then
  echo "Generate at: ${INFRA_APPS_URL}/ui/apps/dynatrace.classic.tokens  (scope: InstallerDownload)"
  read -rs -p 'Paste PaaS token for the infra tenant: ' TF_VAR_dt_paas_token
  echo
  export TF_VAR_dt_paas_token
fi
[ -n "$TF_VAR_dt_paas_token" ] || die "no PaaS token"

if [ -z "${DT_API_TOKEN:-}" ]; then
  echo "Generate at: ${OTLP_APPS_URL}/ui/apps/dynatrace.classic.tokens  (scope: openTelemetryTrace.ingest)"
  read -rs -p 'Paste API token for the OTLP tenant: ' DT_API_TOKEN
  echo
  export DT_API_TOKEN
fi
[ -n "$DT_API_TOKEN" ] || die "no API token"
echo "tenant + tokens set in env"

banner "2. prereqs"
for bin in terraform az ssh-keygen ssh curl; do
  command -v "$bin" >/dev/null || die "missing: $bin"
done
az account show >/dev/null 2>&1 || die "az not logged in; run 'az login'"
# Probe an ARM token explicitly — catches MFA-expired sessions before terraform does.
az account get-access-token --resource https://management.azure.com/ --query expiresOn -o tsv >/dev/null 2>&1 \
  || die "Azure session expired (MFA refresh required); run 'az login' and re-run this script"
echo "all prereqs present"

banner "3. SSH key"
if [ ! -f "$KEY" ]; then
  ssh-keygen -t rsa -b 4096 -f "$KEY" -N "" -C "fieldops-demo" -q
  echo "generated $KEY"
else
  echo "reusing $KEY"
fi
PUBKEY="$(cat "$KEY.pub")"

banner "4. tfvars"
[ -f "$TFVARS" ] || die "$TFVARS not found; create it from README example"
CUR_IP="$(curl -fsS ifconfig.me)/32"
sed -i.bak "s|^allowed_ip[[:space:]]*=.*|allowed_ip         = \"$CUR_IP\"|" "$TFVARS"
sed -i.bak "s|^ssh_public_key[[:space:]]*=.*|ssh_public_key     = \"$PUBKEY\"|" "$TFVARS"
rm -f "$TFVARS.bak"
echo "tfvars synced: allowed_ip=$CUR_IP, ssh_public_key=$KEY.pub"

banner "5. terraform apply"
cd "$INFRA"
terraform init -input=false -upgrade >/dev/null
terraform apply -auto-approve -input=false

PUBIP="$(terraform output -raw public_ip)"
URL="$(terraform output -raw url)"
echo "VM up at $URL  ($PUBIP)"

banner "6. wait for SSH"
for i in {1..60}; do
  ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=3 \
      azureuser@"$PUBIP" 'true' 2>/dev/null && break
  printf '.'; sleep 5
done
echo " ssh up"

banner "7. run deploy.sh on VM"
ssh -i "$KEY" azureuser@"$PUBIP" \
    "sudo DT_URL='$DT_INFRA_URL' \
          DT_TOKEN='$TF_VAR_dt_paas_token' \
          DT_OTLP_ENDPOINT='$DT_OTLP_ENDPOINT' \
          DT_API_TOKEN='$DT_API_TOKEN' \
          bash -s" \
    < "$ROOT/scripts/deploy.sh"

banner "8. smoke test"
sleep 2
CODE="$(curl -sS -o /dev/null -w '%{http_code}' "http://$PUBIP/")"
[ "$CODE" = "200" ] || die "frontend returned HTTP $CODE"
echo "frontend HTTP 200"
EVENTS="$(curl -sS -N -X POST "http://$PUBIP/api/agent/run" \
           -H 'content-type: application/json' \
           -d '{"prompt":"smoke test","role":"technician"}' --max-time 10 \
         | grep -c '^event:' || true)"
[ "$EVENTS" -ge 5 ] || die "SSE returned only $EVENTS events (want >=5)"
echo "SSE stream: $EVENTS events"

banner "done"
DASHBOARD_LINE=""
if [ -n "${DT_DASHBOARD_ID:-}" ]; then
  DASHBOARD_LINE="  Dashboard:  ${INFRA_APPS_URL}/ui/apps/dynatrace.dashboards/dashboard/${DT_DASHBOARD_ID}"$'\n'
fi
cat <<EOF
  URL:        $URL
  SSH:        ssh -i $KEY azureuser@$PUBIP
  AI Obs:     ${OTLP_APPS_URL}/ui/apps/dynatrace.genai.observability
${DASHBOARD_LINE}
  Cost meter is running (~\$0.05/hr). When done:  ./scripts/down.sh
EOF
