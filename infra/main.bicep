// APIS — Azure Container Apps deployment.
//
// What this module provisions:
//   - Azure Container Registry (ACR)
//   - Log Analytics workspace + Container App Environment
//   - Azure Database for PostgreSQL Flexible Server (with PgBouncer enabled)
//   - Azure Cache for Redis (Standard, replicated)
//   - Azure Key Vault (target for the team's external secret store)
//   - User-assigned managed identity with: AcrPull on the registry, secrets get
//     on Key Vault
//   - Two Container Apps: apis-web (HTTP-scaled) and apis-worker (queue-scaled)
//   - One Container App Job: apis-migrate (manual trigger, run before each
//     web/worker rollout)
//
// Secrets are sourced from Key Vault by reference. App env vars listed in
// example.env that aren't sensitive are inlined here; sensitive ones are
// consumed via secretRef.

@description('Environment short name, e.g. stage or prod.')
param environmentName string

@description('Azure region.')
param location string = resourceGroup().location

@description('Container image (e.g. registry.azurecr.io/apis:sha-abc1234).')
param image string

@description('Public hostname for the web app, e.g. registration.example.com. Leave empty to use the auto-generated ACA FQDN.')
param customHostname string = ''

@description('Web app autoscaler bounds.')
param webMinReplicas int = 2
@description('Web app autoscaler bounds.')
param webMaxReplicas int = 30

@description('Worker autoscaler bounds.')
param workerMinReplicas int = 1
@description('Worker autoscaler bounds.')
param workerMaxReplicas int = 20

@description('Postgres SKU. Bump tiers after load testing reveals the hot path.')
param postgresSku string = 'Standard_D2ds_v5'
@description('Postgres storage in GB.')
param postgresStorageGb int = 128

@description('Redis SKU.')
param redisSku string = 'Standard'
@description('Redis capacity (1 -> C1, 2 -> C2, ...).')
param redisCapacity int = 2

@secure()
@description('Postgres administrator password. Stored in Key Vault by the deployer.')
param postgresAdminPassword string

var prefix = 'apis-${environmentName}'
var tags = {
  app: 'apis'
  environment: environmentName
}

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-law'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: replace('${prefix}acr', '-', '')
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    adminUserEnabled: false
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-mi'
  location: location
  tags: tags
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, 'AcrPull')
  scope: acr
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d' // AcrPull
    )
  }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv'
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, identity.id, 'KeyVaultSecretsUser')
  scope: kv
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User
    )
  }
}

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: '${prefix}-pg'
  location: location
  tags: tags
  sku: {
    name: postgresSku
    tier: 'GeneralPurpose'
  }
  properties: {
    version: '16'
    administratorLogin: 'apis'
    administratorLoginPassword: postgresAdminPassword
    storage: {
      storageSizeGB: postgresStorageGb
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: 14
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: environmentName == 'prod' ? 'ZoneRedundant' : 'Disabled'
    }
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pg
  name: 'apis'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Enable PgBouncer at the server level — connection pooler runs on port 6432.
resource pgBouncer 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: pg
  name: 'pgbouncer.enabled'
  properties: {
    value: 'true'
    source: 'user-override'
  }
}

resource pgFwAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: pg
  name: 'AllowAllAzure'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource redis 'Microsoft.Cache/redis@2023-08-01' = {
  name: '${prefix}-redis'
  location: location
  tags: tags
  properties: {
    sku: {
      name: redisSku
      family: 'C'
      capacity: redisCapacity
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

var redisHostname = '${prefix}-redis.redis.cache.windows.net'
var pgHost = pg.properties.fullyQualifiedDomainName

// Container App secret bindings. Values come from Key Vault by reference; the
// deployer is responsible for populating these secret names into Key Vault.
var commonSecrets = [
  { name: 'django-secret-key', keyVaultUrl: '${kv.properties.vaultUri}secrets/django-secret-key', identity: identity.id }
  { name: 'database-pass', keyVaultUrl: '${kv.properties.vaultUri}secrets/database-pass', identity: identity.id }
  { name: 'redis-primary-key', keyVaultUrl: '${kv.properties.vaultUri}secrets/redis-primary-key', identity: identity.id }
  { name: 'square-application-id', keyVaultUrl: '${kv.properties.vaultUri}secrets/square-application-id', identity: identity.id }
  { name: 'square-application-secret', keyVaultUrl: '${kv.properties.vaultUri}secrets/square-application-secret', identity: identity.id }
  { name: 'square-access-token', keyVaultUrl: '${kv.properties.vaultUri}secrets/square-access-token', identity: identity.id }
  { name: 'square-location-id', keyVaultUrl: '${kv.properties.vaultUri}secrets/square-location-id', identity: identity.id }
  { name: 'paypal-client-id', keyVaultUrl: '${kv.properties.vaultUri}secrets/paypal-client-id', identity: identity.id }
  { name: 'paypal-client-secret', keyVaultUrl: '${kv.properties.vaultUri}secrets/paypal-client-secret', identity: identity.id }
  { name: 'email-host-password', keyVaultUrl: '${kv.properties.vaultUri}secrets/email-host-password', identity: identity.id }
  { name: 'sentry-dsn', keyVaultUrl: '${kv.properties.vaultUri}secrets/sentry-dsn', identity: identity.id }
  { name: 'mqtt-jwt-secret', keyVaultUrl: '${kv.properties.vaultUri}secrets/mqtt-jwt-secret', identity: identity.id }
]

var commonEnv = [
  { name: 'DJANGO_SECRET_KEY', secretRef: 'django-secret-key' }
  { name: 'DJANGO_DEBUG', value: 'False' }
  { name: 'DJANGO_LOGLEVEL', value: 'info' }
  { name: 'ALLOWED_HOSTS', value: empty(customHostname) ? '*' : customHostname }
  { name: 'CSRF_TRUSTED_ORIGINS', value: empty(customHostname) ? '*' : 'https://${customHostname}' }
  // Postgres via PgBouncer (port 6432).
  { name: 'DATABASE_HOST', value: pgHost }
  { name: 'DATABASE_PORT', value: '6432' }
  { name: 'DATABASE_NAME', value: 'apis' }
  { name: 'DATABASE_USER', value: 'apis' }
  { name: 'DATABASE_PASS', secretRef: 'database-pass' }
  { name: 'DJANGO_DATABASE_POOL', value: 'False' } // PgBouncer handles pooling
  // Redis. ``redis-primary-key`` is the access key from Azure Cache for Redis;
  // we synthesize the URL inline for TLS on port 6380.
  { name: 'DJANGO_REDIS_URL', value: 'rediss://:$(REDIS_KEY)@${redisHostname}:6380/1?ssl_cert_reqs=required' }
  { name: 'CELERY_BROKER_URL', value: 'rediss://:$(REDIS_KEY)@${redisHostname}:6380/2?ssl_cert_reqs=required' }
  { name: 'CELERY_RESULT_BACKEND', value: 'rediss://:$(REDIS_KEY)@${redisHostname}:6380/2?ssl_cert_reqs=required' }
  { name: 'IDEMPOTENCY_KEY_LOCK_LOCATION', value: 'rediss://:$(REDIS_KEY)@${redisHostname}:6380' }
  { name: 'REDIS_KEY', secretRef: 'redis-primary-key' }
  // Payments
  { name: 'SQUARE_APPLICATION_ID', secretRef: 'square-application-id' }
  { name: 'SQUARE_APPLICATION_SECRET', secretRef: 'square-application-secret' }
  { name: 'SQUARE_ACCESS_TOKEN', secretRef: 'square-access-token' }
  { name: 'SQUARE_LOCATION_ID', secretRef: 'square-location-id' }
  { name: 'PAYPAL_CLIENT_ID', secretRef: 'paypal-client-id' }
  { name: 'PAYPAL_CLIENT_SECRET', secretRef: 'paypal-client-secret' }
  // Email
  { name: 'EMAIL_HOST_PASSWORD', secretRef: 'email-host-password' }
  // Sentry
  { name: 'SENTRY_ENABLED', value: 'True' }
  { name: 'SENTRY_DSN', secretRef: 'sentry-dsn' }
  { name: 'SENTRY_ENVIRONMENT', value: environmentName }
  // MQTT
  { name: 'MQTT_JWT_SECRET', secretRef: 'mqtt-jwt-secret' }
  // Environment label shown in admin UI
  { name: 'ENVIRONMENT_NAME', value: environmentName == 'prod' ? 'Production' : 'Stage' }
  { name: 'ENVIRONMENT_COLOR', value: environmentName == 'prod' ? '#cc0000' : '#cc6600' }
]

resource webApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-web'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
        allowInsecure: false
        traffic: [
          { weight: 100, latestRevision: true }
        ]
      }
      registries: [
        { server: acr.properties.loginServer, identity: identity.id }
      ]
      secrets: commonSecrets
    }
    template: {
      containers: [
        {
          name: 'web'
          image: image
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: commonEnv
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 80 }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/readyz', port: 80 }
              initialDelaySeconds: 5
              periodSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Startup'
              httpGet: { path: '/healthz', port: 80 }
              initialDelaySeconds: 5
              periodSeconds: 5
              failureThreshold: 24
            }
          ]
        }
      ]
      scale: {
        minReplicas: webMinReplicas
        maxReplicas: webMaxReplicas
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

resource workerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-worker'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        { server: acr.properties.loginServer, identity: identity.id }
      ]
      secrets: commonSecrets
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: image
          command: [ '/app/start.sh' ]
          args: [ 'worker' ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: commonEnv
        }
      ]
      scale: {
        minReplicas: workerMinReplicas
        maxReplicas: workerMaxReplicas
        rules: [
          {
            name: 'celery-queue'
            custom: {
              type: 'redis'
              metadata: {
                address: '${redisHostname}:6380'
                listName: 'celery'
                listLength: '20'
                enableTLS: 'true'
              }
              auth: [
                { secretRef: 'redis-primary-key', triggerParameter: 'password' }
              ]
            }
          }
        ]
      }
    }
  }
}

resource migrateJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-migrate'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        { server: acr.properties.loginServer, identity: identity.id }
      ]
      secrets: commonSecrets
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: image
          command: [ '/app/start.sh' ]
          args: [ 'migrate' ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: commonEnv
        }
      ]
    }
  }
}

output acrLoginServer string = acr.properties.loginServer
output webFqdn string = webApp.properties.configuration.ingress.fqdn
output postgresHost string = pgHost
output redisHost string = redisHostname
output keyVaultName string = kv.name
output managedIdentityId string = identity.id
output migrateJobName string = migrateJob.name
