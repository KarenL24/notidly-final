#!/usr/bin/env bash
# One-time setup: creates the Lightsail container service.
# Run this once from your local machine before the first GitHub Actions deploy.
# Requires: AWS CLI v2 + Lightsail plugin installed and `aws configure` done.

set -euo pipefail

SERVICE_NAME="${1:-notidly}"
REGION="${2:-us-east-1}"
# Recommended: medium (4 GB RAM / 2 vCPU) — torch + whisper + tensorflow need ~2-3 GB at peak
POWER="medium"
SCALE=1

echo "Creating Lightsail container service '$SERVICE_NAME' in $REGION ($POWER x$SCALE)..."

aws lightsail create-container-service \
  --service-name "$SERVICE_NAME" \
  --power "$POWER" \
  --scale "$SCALE" \
  --region "$REGION"

echo ""
echo "Done. Service is being provisioned (takes ~2 min)."
echo "Check status: aws lightsail get-container-services --service-name $SERVICE_NAME --region $REGION"
echo ""
echo "Next: add these secrets to your GitHub repo (Settings > Secrets > Actions):"
echo "  AWS_ACCESS_KEY_ID     — your IAM key"
echo "  AWS_SECRET_ACCESS_KEY — your IAM secret"
echo "  AWS_REGION            — $REGION"
echo "  LIGHTSAIL_SERVICE_NAME — $SERVICE_NAME"
