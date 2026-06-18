#!/usr/bin/env bash
# scripts/down.sh — destroy the Azure infra. Safe to re-run.
#
# Notes on what is NOT cleaned up automatically:
#   - Dynatrace dashboard at https://yuf3378h.sprint.apps.dynatracelabs.com (delete in UI if you want)
#   - Dynatrace web application "FieldOps-Demo" (delete in UI if you want)
#   - ~/.ssh/fieldops_rsa private key (kept so up.sh stays consistent)
# These survive across up/down cycles by design.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA="$ROOT/infra"

banner() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
die()    { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

cd "$INFRA"

# Token is required by the provider even for destroy (it's a required var).
# If not set, supply a throwaway value — destroy doesn't actually use it.
export TF_VAR_dt_paas_token="${TF_VAR_dt_paas_token:-unused-for-destroy}"

banner "current state"
if ! terraform state list 2>/dev/null | grep -q .; then
  echo "no state — nothing to destroy"
  exit 0
fi
terraform state list

banner "terraform destroy"
terraform destroy -auto-approve -input=false

banner "done"
echo "Azure resource group removed. Cost meter stopped."
echo ""
echo "Survivors (manual cleanup if desired):"
echo "  - Dynatrace dashboard: https://yuf3378h.sprint.apps.dynatracelabs.com/ui/apps/dynatrace.dashboards/dashboard/7105baa7-5608-465e-874e-69c1ae0781e2"
echo "  - Dynatrace web app 'FieldOps-Demo' in Frontend Observability"
echo "  - ~/.ssh/fieldops_rsa (reused by up.sh)"
