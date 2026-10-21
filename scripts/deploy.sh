#!/usr/bin/env bash
# Deploy Rundown to Azure: infrastructure, then the image, then the app on that image.
#
#   scripts/deploy.sh            (needs az; logs in with the service principal in the environment)
#
# Reads AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID,
# AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_SPEECH_REGION.
# RG (default rundown-rg) and MIN_REPLICAS (default 0) can be set too.
set -euo pipefail
cd "$(dirname "$0")/.."
RG=${RG:-rundown-rg}
MIN_REPLICAS=${MIN_REPLICAS:-0}
TAG=$(git rev-parse --short HEAD)$(git diff --quiet || echo -dirty)

if ! az account show >/dev/null 2>&1; then
  az login --service-principal -u "$AZURE_CLIENT_ID" -p "$AZURE_CLIENT_SECRET" --tenant "$AZURE_TENANT_ID" --output none
fi
az account set --subscription "$AZURE_SUBSCRIPTION_ID"
# a pinned Bicep comes straight from GitHub; the unpinned install goes through aka.ms, which the
# cloud session's network blocks
az bicep version >/dev/null 2>&1 || az bicep install --version v0.30.23

params=(openAiEndpoint="$AZURE_OPENAI_ENDPOINT" openAiKey="$AZURE_OPENAI_API_KEY"
        openAiDeployment="${AZURE_OPENAI_DEPLOYMENT:-gpt-4.1-mini}" speechRegion="${AZURE_SPEECH_REGION:-swedencentral}"
        minReplicas="$MIN_REPLICAS")

echo "1/3 infrastructure"
acr=$(az deployment group create -g "$RG" -n infra -f infra/main.bicep -p "${params[@]}" \
        --query properties.outputs.acrName.value -o tsv)

echo "2/3 image ($TAG), built in the registry"
az acr build -r "$acr" -t "rundown:$TAG" . --output none
server=$(az acr show -n "$acr" --query loginServer -o tsv)

echo "3/3 app on rundown:$TAG"
url=$(az deployment group create -g "$RG" -n app -f infra/main.bicep -p "${params[@]}" image="$server/rundown:$TAG" \
        --query properties.outputs.url.value -o tsv)
echo "live: $url"
curl -fsS --retry 10 --retry-delay 6 --retry-all-errors "$url/api/health" && echo
