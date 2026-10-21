# Deploying Rundown to Azure

**Live:** https://rundown.mangowave-7522f242.swedencentral.azurecontainerapps.io (one warm replica)

Everything goes into one resource group (default `rundown-rg`, Sweden Central), described in
`infra/main.bicep`:

| Resource | Why |
|---|---|
| Container Apps (one app, one replica) | runs the API, the agent crew and the web app, from one image |
| Container Registry (Basic) | holds that image; the image is built in the registry, no local Docker needed |
| Application Insights (+ Log Analytics) | Agent Framework's traces; Foundry tracing reads them |
| Cosmos DB (serverless) | rundowns and the crew's trail (`docs`, `trail` containers) |
| Event Hubs (Basic) | the live match stream (`match-events`) |

The models and Speech are the existing Foundry resource; its endpoint and key are passed in and stored
as a Container Apps secret.

## Cloud session network
The session's network must allow `*.azurecr.io`, `*.blob.core.windows.net`, `*.azurecontainerapps.io`,
`*.documents.azure.com`, `*.servicebus.windows.net`, `*.applicationinsights.azure.com` and
`*.monitor.azure.com`. `aka.ms` stays blocked, so the script installs a pinned Bicep from GitHub.
For the local image build (below) it also needs `deb.debian.org`. Docker Hub rate-limits the session,
so pull the two base images from its mirror first and tag them with their usual names:
`docker pull mirror.gcr.io/library/node:22-slim && docker tag mirror.gcr.io/library/node:22-slim node:22-slim`
(and the same for `python:3.12-slim-bookworm`).

## One-time subscription setup
The subscription must have these resource providers registered: Microsoft.App, ContainerRegistry,
DocumentDB, EventHub, OperationalInsights and Insights. The service principal can't register them
(that needs rights over the whole subscription), so the subscription owner runs
`az provider register -n <namespace>` for each, once, in Cloud Shell. Done 21 Oct.

## Deploy
```bash
scripts/deploy.sh
```
It logs in with the service principal (`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`), deploys the infrastructure, builds the image in the registry
(`az acr build`), points the app at it and checks `/api/health`. It prints the live URL.

This subscription refuses ACR Tasks (`TasksOperationsNotAllowed`, usual on free trials), so
`az acr build` fails and the script falls back to a local Docker build and push. In a cloud session,
start `dockerd`, then run `PROXY_CA=/root/.ccr/ca-bundle.crt MIN_REPLICAS=1 scripts/deploy.sh`:
the build then uses `Dockerfile.proxy`, which trusts the proxy only during the npm, apt and pip steps.
`MIN_REPLICAS=1` keeps the app warm (no cold start) for judging.

The service principal is a Contributor on the resource group only. Contributors can't grant roles,
so the app pulls its image with the registry's admin credentials rather than a managed identity.

The same script runs from GitHub Actions (`.github/workflows/deploy.yml`, manual) once the six
secrets it lists are added to the repository.

## Not wired in yet
The infrastructure exists, but the app doesn't use all of it yet: runs and the trail are still in
memory (hence one replica), and the match stream doesn't go through Event Hubs. The app already
sends traces to Application Insights when the connection string is set, and the Bicep sets it.

## Cleaning up
`az group delete -n rundown-rg` removes everything. The Foundry resource is not in this group and is
not touched.
