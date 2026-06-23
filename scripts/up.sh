#!/usr/bin/env bash
# scripts/up.sh — bring up the FieldOps demo end-to-end.
#
# Usage:
#   export TF_VAR_dt_paas_token='dt0c01....'   # or you'll be prompted
#   ./scripts/up.sh
#
# What this does (idempotent — safe to re-run):
#   1. Verify prereqs (terraform, az, ssh-keygen, ssh, curl)
#   2. Generate ~/.ssh/fieldops_rsa if missing (azurerm rejects ed25519)
#   3. Refresh allowed_ip in terraform.tfvars to your current public IP
#   4. terraform apply  (creates RG, VNet, NSG, public IP, VM)
#   5. SSH to the VM and run scripts/deploy.sh (installs OneAgent, Node, Nginx, app)
#   6. Smoke test: curl the public URL, confirm SSE event stream
#
# Token security: the PaaS token is only ever set in your shell env.
# It never lands in tfvars, never on disk, never in chat.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA="$ROOT/infra"
KEY="$HOME/.ssh/fieldops_rsa"
TFVARS="$INFRA/terraform.tfvars"

banner() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
warn()   { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die()    { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

banner "1. PaaS token"
if [ -z "${TF_VAR_dt_paas_token:-}" ]; then
  read -rs -p 'Paste Dynatrace PaaS token (scope InstallerDownload): ' TF_VAR_dt_paas_token
  echo
  export TF_VAR_dt_paas_token
fi
[ -n "$TF_VAR_dt_paas_token" ] || die "no token"
echo "token set in env"

banner "2. prereqs"
for bin in terraform az ssh-keygen ssh curl; do
  command -v "$bin" >/dev/null || die "missing: $bin"
done
az account show >/dev/null 2>&1 || die "az not logged in; run 'az login'"
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
    "sudo DT_URL='https://yuf3378h.sprint.dynatracelabs.com' DT_TOKEN='$TF_VAR_dt_paas_token' bash -s" \
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
cat <<EOF
  URL:        $URL
  SSH:        ssh -i $KEY azureuser@$PUBIP
  Dashboard:  https://yuf3378h.sprint.apps.dynatracelabs.com/ui/apps/dynatrace.dashboards/dashboard/7105baa7-5608-465e-874e-69c1ae0781e2

  Cost meter is running (~\$0.05/hr). When done:  ./scripts/down.sh
EOF
