# Deploying Rundown to Azure

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

## Deploy
```bash
scripts/deploy.sh
```
It logs in with the service principal (`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`), deploys the infrastructure, builds the image in the registry
(`az acr build`), points the app at it and checks `/api/health`. It prints the live URL.
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
