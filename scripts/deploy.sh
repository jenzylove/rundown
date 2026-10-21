#!/usr/bin/env bash
# Deploy Rundown to Azure: infrastructure, then the image, then the app on that image.
#
#   scripts/deploy.sh            (needs az; logs in with the service principal in the environment)
#
# Reads AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID,
# AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_SPEECH_REGION.
# RG (default rundown-rg), MIN_REPLICAS (default 0) and PROXY_CA (see Dockerfile.proxy) can be set too.
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

server=$(az acr show -n "$acr" --query loginServer -o tsv)
echo "2/3 image ($TAG), built in the registry"
if ! az acr build -r "$acr" -t "rundown:$TAG" . --output none; then
  # some subscriptions (free trials) refuse ACR Tasks: build with local Docker and push instead
  echo "    registry build refused; building with local Docker"
  build=(-t "$server/rundown:$TAG")
  # behind the cloud session's HTTPS-only proxy (PROXY_CA=/root/.ccr/ca-bundle.crt): Dockerfile.proxy
  [ -n "${PROXY_CA:-}" ] && build+=(-f Dockerfile.proxy --network host --secret "id=proxyca,src=$PROXY_CA"
                                    --build-arg "https_proxy=${HTTPS_PROXY:-}" --build-arg "no_proxy=${NO_PROXY:-}")
  docker build "${build[@]}" .
  az acr credential show -n "$acr" --query "passwords[0].value" -o tsv \
    | docker login "$server" -u "$(az acr credential show -n "$acr" --query username -o tsv)" --password-stdin
  docker push "$server/rundown:$TAG"
fi

echo "3/3 app on rundown:$TAG"
url=$(az deployment group create -g "$RG" -n app -f infra/main.bicep -p "${params[@]}" image="$server/rundown:$TAG" \
        --query properties.outputs.url.value -o tsv)
echo "live: $url"
curl -fsS --retry 10 --retry-delay 6 --retry-all-errors "$url/api/health" && echo
