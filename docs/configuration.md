# Configuração

## `.env`

O `.env` define conexão, autenticação e comportamento global da aplicação.

### Variáveis suportadas

| Variável | Obrigatória | Default | Descrição |
| --- | --- | --- | --- |
| `APP_CONFIG_PATH` | não | `config.demo.yaml` | Caminho padrão do YAML. |
| `APP_LOG_LEVEL` | não | `INFO` | Nível de log. |
| `APP_LOG_FORMAT` | não | `text` | `text` ou `json`. |
| `APP_UUID_NAMESPACE` | não | `6f0ec5a9-4e76-4cd4-a75e-bfc8f3dbcd55` | Namespace usado para `uuid5`. |
| `ADX_CLUSTER_URL` | sim | - | URL do cluster ADX. |
| `ADX_DATABASE` | sim | - | Database alvo no ADX. |
| `ADX_AUTH_MODE` | não | `default` | `default`, `managed_identity`, `application_key` ou `az_cli`. |
| `ADX_AUTHORITY_ID` | não | vazio | Tenant/authority para `application_key` quando necessário. |
| `ADX_MANAGED_IDENTITY_CLIENT_ID` | não | vazio | Client ID da managed identity user-assigned. |
| `ADX_CLIENT_ID` | depende | vazio | Obrigatório com `application_key`. |
| `ADX_CLIENT_SECRET` | depende | vazio | Obrigatório com `application_key`. |
| `ADX_QUERY_TIMEOUT_SECONDS` | não | `30` | Reservado para evolução futura do cliente ADX. |
| `NEO4J_URI` | não em `dry_run` | vazio | URI do Neo4j. |
| `NEO4J_DATABASE` | não | `neo4j` | Database do Neo4j. |
| `NEO4J_USERNAME` | não | `neo4j` | Usuário do Neo4j. |
| `NEO4J_PASSWORD` | não em `dry_run` | vazio | Senha do Neo4j. |
| `NEO4J_TIMEOUT_SECONDS` | não | `10` | Timeout de conexão. |
| `NEO4J_VERIFY_CONNECTIVITY` | não | `true` | Valida conectividade no bootstrap. |
| `NEO4J_APPLY_SCHEMA` | não | `true` | Tenta criar constraints e índice no startup. |

## YAML

O YAML tem dois blocos na raiz:

- `runtime`
- `jobs`

### Exemplo mínimo

```yaml
runtime:
  default_interval_seconds: 300
  sleep_seconds: 0
  dry_run: false

jobs:
  - name: signins
    query: |
      SigninLogs
      | project UserPrincipalName, IPAddress
    nodes:
      - type: User
        template_hashes:
          - user-v1
        column_properties:
          name: UserPrincipalName
```

## `runtime`

| Campo | Tipo | Default | Descrição |
| --- | --- | --- | --- |
| `default_interval_seconds` | inteiro | `60` | Intervalo aplicado aos jobs sem intervalo próprio. |
| `sleep_seconds` | número | `0` | Pausa entre linhas processadas. |
| `dry_run` | boolean | `false` | Não grava no Neo4j. |

## `jobs[]`

| Campo | Obrigatório | Descrição |
| --- | --- | --- |
| `name` | sim | Nome lógico do job. |
| `query` | sim | Query KQL executada no ADX. |
| `interval_seconds` | não | Intervalo específico do job. |
| `nodes` | não | Templates de node avaliados por linha. |
| `relationships` | não | Templates de relacionamento avaliados por linha. |

## `nodes[]`

| Campo | Obrigatório | Descrição |
| --- | --- | --- |
| `type` ou `types` | sim | Labels técnicas do node. |
| `template_hashes` | sim | Lista de hashes do template. |
| `update_policy` | não | `create`, `merge` ou `merge_at_change`. |
| `static_properties` | não | Propriedades literais. |
| `column_properties` | não | Mapa `propriedade -> coluna`. |
| `conditional_properties` | não | Propriedades aplicadas sob condição. |
| `conditions` | não | Filtro para decidir se o node será gerado. |

Regra obrigatória:

- `name` precisa existir em `static_properties` ou `column_properties`

## `relationships[]`

| Campo | Obrigatório | Descrição |
| --- | --- | --- |
| `type` | sim | Tipo técnico do relacionamento. |
| `template_hash` | sim | Hash canônico da definição do relacionamento. |
| `update_policy` | não | `create`, `merge` ou `merge_at_change`. |
| `static_properties` | não | Propriedades literais. |
| `column_properties` | não | Mapa `propriedade -> coluna`. |
| `conditional_properties` | não | Propriedades aplicadas sob condição. |
| `conditions` | não | Filtro do template. |
| `source` | sim | Como localizar o node de origem. |
| `target` | sim | Como localizar o node de destino. |

## `source` e `target`

```yaml
source:
  type: User
  match_attributes:
    static:
      origin: auto
    columns:
      name: UserPrincipalName
```

Pelo menos um atributo de match precisa existir.

## Condições

### String

```yaml
conditions:
  - type: string
    column: Severity
    equals: High
```

Operadores válidos:

- `equals`
- `not_equals`

### Number

```yaml
conditions:
  - type: number
    column: FailedAttempts
    greater_than: 5
```

Operadores válidos:

- `equals`
- `not_equals`
- `greater_than`
- `less_than`

## `conditional_properties`

### Valor estático

```yaml
conditional_properties:
  - type: static
    name: risk
    value: high
    conditions:
      - type: number
        column: FailedAttempts
        greater_than: 5
```

### Valor vindo de coluna

```yaml
conditional_properties:
  - type: column
    name: geo
    from_column: Country
    conditions:
      - type: string
        column: Country
        not_equals: ""
```

## Aliases aceitos

- `nodes[].type` vira `types` com um item
- `relationships[].template_hashes` com um único item vira `template_hash`
- `mergeAtChange` e `merge-at-change` viram `merge_at_change`
- `dynamic_properties` é aceito como alias de `column_properties`
- `match_static_attributes` e `match_column_attributes` são aceitos como aliases de `match_attributes`
