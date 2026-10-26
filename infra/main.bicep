// Rundown on Azure: everything in one resource group.
//   Container Apps   the API, crew and web app (one image)
//   Container Registry  where that image lives
//   Application Insights  traces from Agent Framework, read by Foundry tracing
//   Cosmos DB (serverless)  rundowns and the crew's trail
//   Event Hubs        the live match stream
// The models and Speech are the existing Foundry resource; its endpoint and key come in as parameters.

@description('Short name used in every resource name.')
param name string = 'rundown'
param location string = resourceGroup().location

@description('Image to run. The first deploy uses a public placeholder; scripts/deploy.sh then builds and points at ours.')
param image string = 'mcr.microsoft.com/k8se/quickstart:latest'  // keep in step with `placeholder` below

@description('Keep one replica warm (no cold start for judges) or scale to zero when idle.')
param minReplicas int = 0

param openAiEndpoint string
param openAiDeployment string = 'gpt-4.1-mini'
@secure()
param openAiKey string
param speechRegion string = 'swedencentral'

var suffix = uniqueString(resourceGroup().id)
var placeholder = 'mcr.microsoft.com/k8se/quickstart:latest'
var usesAcr = image != placeholder

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs-${suffix}'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${name}-insights-${suffix}'
  location: location
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${name}acr${suffix}'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }  // the deploy identity can't grant AcrPull, so the app pulls with these
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: '${name}-cosmos-${suffix}'
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [ { locationName: location, failoverPriority: 0 } ]
    capabilities: [ { name: 'EnableServerless' } ]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
  }
}

resource db 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'rundown'
  properties: { resource: { id: 'rundown' } }
}

resource containers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [for c in [ 'docs', 'trail' ]: {
  parent: db
  name: c
  properties: { resource: { id: c, partitionKey: { paths: [ '/runId' ], kind: 'Hash' } } }
}]

resource hubs 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: '${name}-hubs-${suffix}'
  location: location
  sku: { name: 'Basic', tier: 'Basic', capacity: 1 }
}

resource matchHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: hubs
  name: 'match-events'
  properties: { partitionCount: 2, messageRetentionInDays: 1 }
}

resource hubAuth 'Microsoft.EventHub/namespaces/authorizationRules@2024-01-01' existing = {
  parent: hubs
  name: 'RootManageSharedAccessKey'
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env-${suffix}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: usesAcr ? 8000 : 80, transport: 'auto', allowInsecure: false }
      secrets: [
        { name: 'openai-key', value: openAiKey }
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
        { name: 'cosmos-key', value: cosmos.listKeys().primaryMasterKey }
        { name: 'hubs-connection', value: hubAuth.listKeys().primaryConnectionString }
      ]
      registries: usesAcr ? [ { server: acr.properties.loginServer, username: acr.listCredentials().username, passwordSecretRef: 'acr-password' } ] : []
    }
    template: {
      containers: [ {
        name: 'rundown'
        image: image
        resources: { cpu: json('1.0'), memory: '2Gi' }
        env: [
          { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
          { name: 'AZURE_OPENAI_DEPLOYMENT', value: openAiDeployment }
          { name: 'AZURE_OPENAI_API_KEY', secretRef: 'openai-key' }
          { name: 'AZURE_SPEECH_REGION', value: speechRegion }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
          { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
          { name: 'COSMOS_KEY', secretRef: 'cosmos-key' }
          { name: 'EVENTHUB_CONNECTION', secretRef: 'hubs-connection' }
          { name: 'EVENTHUB_NAME', value: matchHub.name }
          { name: 'RUNDOWN_PUBLIC_HOST', value: '${name}.${env.properties.defaultDomain}' }  // the MCP server's allowed host
        ]
        probes: usesAcr ? [
          { type: 'Liveness', httpGet: { path: '/api/health', port: 8000 }, periodSeconds: 30 }
          { type: 'Readiness', httpGet: { path: '/api/health', port: 8000 }, periodSeconds: 10 }
        ] : []
      } ]
      // runs live in memory until the Cosmos store lands, so one replica holds them all
      scale: { minReplicas: minReplicas, maxReplicas: 1, rules: [ { name: 'http', http: { metadata: { concurrentRequests: '100' } } } ] }
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output acrName string = acr.name
output acrServer string = acr.properties.loginServer
output appInsights string = insights.name
