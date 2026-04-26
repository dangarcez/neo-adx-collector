# Referencia de Configuracao

Este documento descreve o contrato de configuracao atualmente suportado pelo coletor ADX, cobrindo:

- variaveis de ambiente em `.env`
- estrutura e semantica do arquivo YAML
- defaults aplicados pelo bootstrap
- normalizacoes aceitas pelo parser
- regras de validacao que fazem o startup falhar

O objetivo aqui e servir como referencia operacional e de modelagem. Para detalhes de arquitetura, consulte [architecture.md](./architecture.md).

## Visao Geral

O coletor combina dois conjuntos de configuracao:

- `.env`: parametros operacionais, autenticacao, logs e conectividade
- YAML: jobs de consulta ao ADX e regras de criacao e atualizacao no Neo4j

Em termos praticos:

- `.env` define como a aplicacao roda e como se conecta ao ADX e ao Neo4j
- YAML define o que a aplicacao consulta no ADX e como cada linha retornada vira mutacoes no grafo

Importante:

- o contrato atual do projeto ainda usa uma raiz simples com `runtime` e `jobs`
- o formato generico baseado em `sources` nao foi adotado neste coletor
- as propriedades dinamicas seguem a nomenclatura atual do projeto: `column_properties`

## Ordem de precedencia

Ao iniciar:

1. o processo carrega o arquivo `.env` informado pela flag `--env`
2. cada variavel do `.env` so e aplicada se ainda nao existir no ambiente do processo
3. o caminho do YAML vem da flag `--config` ou, se ausente, de `APP_CONFIG_PATH`
4. o YAML e validado antes da aplicacao iniciar o loop ou executar `--once`

Isso significa que uma variavel exportada no shell tem precedencia sobre o `.env`.

## Referencia do `.env`

### Variaveis suportadas

| Variavel | Default | Obrigatoria | Descricao |
| --- | --- | --- | --- |
| `APP_CONFIG_PATH` | `config.demo.yaml` | nao | Caminho padrao do arquivo YAML quando `--config` nao e informado. |
| `APP_LOG_LEVEL` | `INFO` | nao | Nivel de log. Valores usuais: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `APP_LOG_FORMAT` | `text` | nao | Formato de log. Suporta `text` e `json`. |
| `APP_UUID_NAMESPACE` | `6f0ec5a9-4e76-4cd4-a75e-bfc8f3dbcd55` | nao | Namespace usado para gerar `uuid5` de nodes e relacionamentos. |
| `ADX_CLUSTER_URL` | vazio | sim | URL do cluster Azure Data Explorer. |
| `ADX_DATABASE` | vazio | sim | Database alvo no ADX. |
| `ADX_AUTH_MODE` | `default` | nao | Modo de autenticacao. Valores suportados: `default`, `managed_identity`, `application_key`, `az_cli`. |
| `ADX_AUTHORITY_ID` | vazio | nao | Tenant ou authority para autenticacao por application key. |
| `ADX_MANAGED_IDENTITY_CLIENT_ID` | vazio | nao | Client ID da managed identity user-assigned quando aplicavel. |
| `ADX_CLIENT_ID` | vazio | depende | Obrigatorio quando `ADX_AUTH_MODE=application_key`. |
| `ADX_CLIENT_SECRET` | vazio | depende | Obrigatorio quando `ADX_AUTH_MODE=application_key`. |
| `ADX_QUERY_TIMEOUT_SECONDS` | `30` | nao | Reservado para evolucao futura do cliente ADX. No estado atual ainda nao altera a execucao da query. |
| `NEO4J_URI` | vazio | depende | URI do Neo4j. Em `dry_run`, pode nao ser necessaria. |
| `NEO4J_DATABASE` | `neo4j` | nao | Database alvo no Neo4j. |
| `NEO4J_USERNAME` | `neo4j` | nao | Usuario do Neo4j. |
| `NEO4J_PASSWORD` | vazio | depende | Senha do Neo4j. Obrigatoria quando houver escrita real no banco. |
| `NEO4J_TIMEOUT_SECONDS` | `10` | nao | Timeout de conectividade com Neo4j em segundos. |
| `NEO4J_VERIFY_CONNECTIVITY` | `true` | nao | Se `true`, valida conectividade no startup. |
| `NEO4J_APPLY_SCHEMA` | `true` | nao | Se `true`, tenta criar constraints e indice no startup. |

### Exemplo de `.env`

```dotenv
APP_CONFIG_PATH=config.demo.yaml
APP_LOG_LEVEL=INFO
APP_LOG_FORMAT=text
APP_UUID_NAMESPACE=6f0ec5a9-4e76-4cd4-a75e-bfc8f3dbcd55

ADX_CLUSTER_URL=https://my-cluster.kusto.windows.net
ADX_DATABASE=my_database
ADX_AUTH_MODE=default
ADX_AUTHORITY_ID=
ADX_MANAGED_IDENTITY_CLIENT_ID=
ADX_CLIENT_ID=
ADX_CLIENT_SECRET=
ADX_QUERY_TIMEOUT_SECONDS=30

NEO4J_URI=bolt://localhost:7687
NEO4J_DATABASE=neo4j
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=neo4j123
NEO4J_TIMEOUT_SECONDS=10
NEO4J_VERIFY_CONNECTIVITY=true
NEO4J_APPLY_SCHEMA=true
```

### Observacoes sobre `.env`

- linhas vazias e comentarios com `#` sao ignorados
- o parser aceita linhas com ou sem `export`
- valores entre aspas simples ou duplas sao suportados
- o arquivo `.env` nao sobrescreve variaveis ja presentes no ambiente do processo
- quando `ADX_AUTH_MODE=default`, o fluxo depende do que o `DefaultAzureCredential` conseguir encontrar no ambiente

## Estrutura do YAML

O arquivo YAML tem dois blocos principais na raiz:

- `runtime`
- `jobs`

### Exemplo completo

```yaml
runtime:
  default_interval_seconds: 300
  sleep_seconds: 0
  dry_run: false

jobs:
  - name: failed_signins
    query: |
      SigninLogs
      | where TimeGenerated > ago(15m)
      | summarize FailedAttempts = count(), LastFailure = max(TimeGenerated) by UserPrincipalName, IPAddress, AppDisplayName
    interval_seconds: 300
    nodes:
      - types:
          - User
        template_hashes:
          - user-v1
        update_policy: merge
        expiration_time_min: 60
        static_properties:
          source_system: adx
          category: identity
        column_properties:
          name: UserPrincipalName
          last_failure_at: LastFailure
        conditional_properties:
          - type: static
            name: risk
            value: high
            conditions:
              - type: number
                column: FailedAttempts
                greater_than: 5
        property_transforms:
          - property: name
            process:
              - type: TO_LOWER

      - type: IPAddress
        template_hashes:
          - ip-address-v1
        update_policy: merge
        static_properties:
          source_system: adx
        column_properties:
          name: IPAddress

    relationships:
      - type: AUTHENTICATED_FROM
        template_hash: user-authenticated-from-ip-v1
        update_policy: merge
        expiration_time_min: 15
        static_properties:
          source_system: adx
        column_properties:
          app_display_name: AppDisplayName
          failed_attempts: FailedAttempts
        property_transforms:
          - property: app_display_name
            process:
              - type: TO_UPPER
        source:
          type: User
          match_attributes:
            columns:
              name: UserPrincipalName
        target:
          type: IPAddress
          match_attributes:
            columns:
              name: IPAddress
```

## Raiz: `runtime`

O bloco `runtime` define comportamento operacional compartilhado por todos os jobs.

### Campos

| Campo | Tipo | Obrigatorio | Default | Descricao |
| --- | --- | --- | --- | --- |
| `default_interval_seconds` | inteiro | nao | `60` | Intervalo padrao aplicado a jobs sem `interval_seconds` explicito. |
| `sleep_seconds` | numero | nao | `0` | Pausa em segundos apos o processamento de cada linha. Aceita fracoes. |
| `dry_run` | boolean | nao | `false` | Se `true`, consulta o ADX e monta os planos, mas nao grava no Neo4j. |

### Comportamento de `dry_run`

Quando `dry_run` esta ativo:

- a query no ADX continua acontecendo
- as condicoes e propriedades continuam sendo resolvidas
- nodes e relacionamentos planejados sao contabilizados como `skipped`
- nenhuma escrita e feita no Neo4j

## Jobs

Bloco: `jobs[]`

Cada job executa uma query KQL e processa cada linha retornada de forma isolada.

### Campos

| Campo | Tipo | Obrigatorio | Default | Descricao |
| --- | --- | --- | --- | --- |
| `name` | string | sim | - | Nome logico do job. |
| `query` | string | sim | - | Query KQL executada no ADX. |
| `interval_seconds` | inteiro | nao | `runtime.default_interval_seconds` | Intervalo entre execucoes do job. |
| `nodes` | lista | nao | lista vazia | Templates de node avaliados para cada linha. |
| `relationships` | lista | nao | lista vazia | Templates de relacionamento avaliados para cada linha. |

### Observacoes

- `interval_seconds` precisa ser maior que zero apos a normalizacao
- um job pode ter apenas nodes, apenas relacionamentos, ou ambos
- os relacionamentos nao criam nodes implicitamente
- um erro no processamento de uma linha nao aborta o job inteiro; a linha e registrada como falha e o loop continua

## Nodes

Bloco: `jobs[].nodes[]`

Cada template de node e avaliado contra cada linha retornada pela query do job.

### Campos

| Campo | Tipo | Obrigatorio | Default | Descricao |
| --- | --- | --- | --- | --- |
| `type` | string | condicional | - | Alias para um unico tipo. |
| `types` | lista de string | condicional | - | Lista de labels tecnicas do node. Deve haver ao menos uma. |
| `template_hashes` | lista de string | sim | - | Lista de hashes de definicao associados ao node. |
| `update_policy` | string | nao | `create` | Politica de persistencia: `create`, `merge` ou `merge_at_change`. |
| `expiration_time_min` | inteiro | nao | ausente | Quando informado, gera `expires_at` como horario atual UTC + esse numero de minutos. So e aplicado em `create` e `merge`. |
| `static_properties` | mapa | nao | `{}` | Propriedades literais copiadas para o node. |
| `column_properties` | mapa string->string | nao | `{}` | Propriedades dinamicas resolvidas a partir das colunas da linha. |
| `conditional_properties` | lista | nao | `[]` | Propriedades aplicadas apenas quando suas condicoes passam. |
| `property_transforms` | lista | nao | `[]` | Processamentos aplicados sobre propriedades ja resolvidas antes dos campos automaticos. |
| `conditions` | lista | nao | `[]` | Filtro para decidir se o template deve ser aplicado a linha. |

### Regras obrigatorias

- deve haver pelo menos um tipo em `types`, ou um `type` que sera normalizado para `types`
- deve haver ao menos um item em `template_hashes`
- a propriedade `name` precisa ser definida em `static_properties` ou `column_properties`

### Exemplo minimo

```yaml
- type: User
  template_hashes:
    - user-v1
  column_properties:
    name: UserPrincipalName
```

### Exemplo com propriedades dinamicas, condicionais e transformacoes

```yaml
- types:
    - User
  template_hashes:
    - user-v1
  update_policy: merge
  expiration_time_min: 60
  static_properties:
    source_system: adx
  column_properties:
    name: UserPrincipalName
    last_failure_at: LastFailure
  conditional_properties:
    - type: static
      name: risk
      value: high
      conditions:
        - type: number
          column: FailedAttempts
          greater_than: 5
  property_transforms:
    - property: name
      process:
        - type: TO_LOWER
```

## Relacionamentos

Bloco: `jobs[].relationships[]`

Cada template de relacionamento e avaliado contra cada linha retornada pela query do job.

### Campos

| Campo | Tipo | Obrigatorio | Default | Descricao |
| --- | --- | --- | --- | --- |
| `type` | string | sim | - | Tipo tecnico do relacionamento no Neo4j. |
| `template_hash` | string | condicional | - | Hash canonico da definicao de relacionamento. |
| `template_hashes` | lista de string | condicional | - | Alias aceito apenas quando houver um unico item; sera normalizado para `template_hash`. |
| `update_policy` | string | nao | `create` | Politica de persistencia: `create`, `merge` ou `merge_at_change`. |
| `expiration_time_min` | inteiro | nao | ausente | Quando informado, gera `expires_at` como horario atual UTC + esse numero de minutos. So e aplicado em `create` e `merge`. |
| `static_properties` | mapa | nao | `{}` | Propriedades literais do relacionamento. |
| `column_properties` | mapa string->string | nao | `{}` | Propriedades dinamicas resolvidas da linha. |
| `conditional_properties` | lista | nao | `[]` | Propriedades aplicadas apenas se as condicoes forem satisfeitas. |
| `property_transforms` | lista | nao | `[]` | Processamentos aplicados sobre propriedades ja resolvidas antes dos campos automaticos. |
| `conditions` | lista | nao | `[]` | Filtro para decidir se o relacionamento deve ser gerado. |
| `source` | objeto | sim | - | Seletor do node de origem. |
| `target` | objeto | sim | - | Seletor do node de destino. |

### Regras obrigatorias

- `type` e obrigatorio
- `template_hash` precisa existir apos a normalizacao
- `source` e `target` precisam ter `type` e pelo menos um atributo de match

### Exemplo minimo

```yaml
- type: AUTHENTICATED_FROM
  template_hash: user-authenticated-from-ip-v1
  source:
    type: User
    match_attributes:
      columns:
        name: UserPrincipalName
  target:
    type: IPAddress
    match_attributes:
      columns:
        name: IPAddress
```

### Observacao importante sobre persistencia

Na configuracao, relacionamento usa `template_hash` singular como entrada canonica. Durante a construcao da mutacao para o grafo, esse valor e persistido como `template_hashes` com um unico item.

Exemplo:

- config: `template_hash: user-authenticated-from-ip-v1`
- propriedade persistida no Neo4j: `template_hashes: ["user-authenticated-from-ip-v1"]`

## Source e Target

Blocos:

- `jobs[].relationships[].source`
- `jobs[].relationships[].target`

Esses blocos definem como localizar os nodes ja existentes que receberao o relacionamento.

### Campos

| Campo | Tipo | Obrigatorio | Descricao |
| --- | --- | --- | --- |
| `type` | string | sim | Label tecnica do node a ser encontrado. |
| `match_attributes.static` | mapa | nao | Atributos fixos usados no match. |
| `match_attributes.columns` | mapa string->string | nao | Atributos resolvidos a partir de colunas da linha. |

Pelo menos um atributo de match deve existir entre `static` e `columns`.

### Comportamento de match

- se nenhum `source` ou nenhum `target` for encontrado, o relacionamento e ignorado
- se houver multiplos `source` e multiplos `target`, o coletor cria o produto cartesiano entre eles
- exemplo: `2 source x 3 target = 6` relacionamentos
- a unica ambiguidade que continua sendo erro e encontrar mais de um relacionamento equivalente ja existente para o mesmo par `source-target`

### Exemplo

```yaml
source:
  type: User
  match_attributes:
    static:
      origin: auto
    columns:
      name: UserPrincipalName
```

### Aliases legados suportados

Ainda sao aceitos os seguintes aliases:

- `match_static_attributes`
- `match_column_attributes`

Eles sao incorporados internamente em `match_attributes`.

Exemplo:

```yaml
target:
  type: IPAddress
  match_column_attributes:
    name: IPAddress
  match_static_attributes:
    origin: auto
```

## Propriedades

Existem quatro formas de definir e ajustar propriedades em nodes e relacionamentos.

Separadamente dessas quatro formas, nodes e relacionamentos tambem podem declarar `expiration_time_min`, que nao escreve uma propriedade de negocio diretamente no planner. Esse campo instrui o repositorio a gerar `expires_at` no momento da escrita, com base no horario atual UTC.

### `static_properties`

Valores literais copiados como estao.

```yaml
static_properties:
  source_system: adx
  category: identity
```

### `column_properties`

Mapa de `nome_da_propriedade -> nome_da_coluna`.

Exemplo:

```yaml
column_properties:
  name: UserPrincipalName
  ip_address: IPAddress
  observed_at: LastFailure
```

Se a coluna nao existir na linha:

- em `column_properties`, a propriedade e omitida
- em `conditional_properties` do tipo `column`, a propriedade tambem e omitida
- essa tolerancia nao se aplica a `match_attributes.columns` de `source` e `target`

### `conditional_properties`

Cada item define uma propriedade que so sera aplicada se todas as condicoes do bloco forem verdadeiras.

Campos:

| Campo | Tipo | Obrigatorio | Descricao |
| --- | --- | --- | --- |
| `type` | string | sim | `static` ou `column`. |
| `name` | string | sim | Nome da propriedade a ser escrita. |
| `value` | qualquer | condicional | Obrigatorio quando `type: static`. |
| `from_column` | string | condicional | Obrigatorio quando `type: column`. |
| `conditions` | lista | sim | Lista de condicoes que habilitam a propriedade. |

Exemplos:

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

### `property_transforms`

Executa uma lista ordenada de processors sobre uma propriedade ja resolvida.

Schema:

```yaml
property_transforms:
  - property: name
    process:
      - type: TO_UPPER
```

Processors suportados:

- `TO_UPPER`
- `TO_LOWER`

Regras:

- roda depois de `static_properties`, `column_properties` e `conditional_properties`
- roda antes dos campos automaticos
- se a propriedade nao existir, o transform e ignorado
- se o valor nao for string, `TO_UPPER` e `TO_LOWER` sao ignorados

### `expiration_time_min`

Campo opcional para node ou relacionamento.

Semantica:

- gera `expires_at = agora_utc + expiration_time_min`
- `expires_at` e gerado apenas na persistencia
- `expires_at` so entra em `create` e `merge`
- `merge_at_change` nao deve renovar `expires_at`

## Condicoes

Condicoes podem ser usadas em:

- `nodes[].conditions`
- `relationships[].conditions`
- `conditional_properties[].conditions`

Todas as condicoes de uma lista precisam passar. O comportamento e um `AND` implicito.

### Condicoes de string

Campos suportados:

| Campo | Obrigatorio | Observacao |
| --- | --- | --- |
| `type: string` | sim | Identifica o tipo da condicao. |
| `column` | sim | Nome da coluna usada na comparacao. |
| `equals` ou `not_equals` | exatamente um | Somente um operador pode ser usado. |

Exemplo:

```yaml
- type: string
  column: Country
  equals: BR
```

Se a coluna nao existir na linha, a condicao resulta em `false`.

### Condicoes numericas

Campos suportados:

| Campo | Obrigatorio | Observacao |
| --- | --- | --- |
| `type: number` | sim | Identifica o tipo da condicao. |
| `column` | sim | Nome da coluna usada na comparacao. |
| `equals`, `not_equals`, `greater_than` ou `less_than` | exatamente um | Somente um operador pode ser usado. |

Exemplo:

```yaml
- type: number
  column: FailedAttempts
  greater_than: 5
```

Comparacoes numericas aceitam inteiros, floats e strings numericas nos operadores `equals` e `not_equals`.

## `update_policy`

Suportado em nodes e relacionamentos.

Valores:

- `create`
- `merge`
- `merge_at_change`

### Semantica

`create`

- cria somente se a entidade equivalente ainda nao existir
- se ja existir, a mutacao e ignorada
- se `expiration_time_min` existir, cria `expires_at` na insercao

`merge`

- cria quando nao existe
- atualiza propriedades quando ja existe
- se `expiration_time_min` existir, cria ou renova `expires_at`

`merge_at_change`

- cria quando nao existe
- compara apenas os atributos definidos no YAML, ignorando campos automaticos
- se nada mudou nos atributos de negocio, nao atualiza a entidade
- se algo mudou, atualiza propriedades e renova `updated_at`
- nao renova `expires_at`

Se o campo for omitido, o default e `create`.

## Defaults e normalizacoes

### Defaults aplicados

- `runtime.default_interval_seconds`: `60`
- `runtime.sleep_seconds`: `0`
- `runtime.dry_run`: `false`
- `jobs[].interval_seconds`: herda `runtime.default_interval_seconds`
- `nodes[].update_policy`: `create`
- `relationships[].update_policy`: `create`
- mapas de propriedades ausentes sao normalizados para objetos vazios
- `conditional_properties` e `property_transforms` ausentes sao normalizados para listas vazias

### Normalizacoes aceitas

- `nodes[].type` e convertido para `nodes[].types` com um item
- `relationships[].template_hashes` com um unico item e convertido para `template_hash`
- `mergeAtChange` e `merge-at-change` sao aceitos como alias de `merge_at_change`
- `dynamic_properties` e aceito como alias de `column_properties`
- aliases legados de match sao incorporados em `match_attributes`
- strings com espacos nas extremidades sao `trimadas` nos campos relevantes

## Regras de validacao que falham no startup

O bootstrap falha antes de iniciar o scheduler quando encontrar erros como:

- `jobs` vazio
- job sem `name`
- job sem `query`
- `interval_seconds <= 0` apos normalizacao
- node sem tipo
- node sem `template_hashes`
- node sem propriedade `name`
- relacionamento sem `type`
- relacionamento sem `template_hash`
- `source` ou `target` sem `type`
- `source` ou `target` sem atributos de match
- `update_policy` fora de `create`, `merge` ou `merge_at_change`
- `expiration_time_min <= 0` quando informado
- `property_transforms` sem `property`, sem `process` ou com processor invalido
- `conditional_properties` sem `name`, `type` valido ou `conditions`
- condicoes com mais de um operador

## Erros comuns de modelagem

### 1. Esquecer `name` em nodes

Sem `name`, o node e rejeitado na validacao. O campo pode vir de:

- `static_properties.name`
- `column_properties.name`

### 2. Usar `template_hashes` com varios itens em relacionamento

O parser aceita `template_hashes` apenas como alias para um unico valor. Para relacionamento, o campo de entrada canonico continua sendo `template_hash`.

### 3. Esperar `expires_at` em `merge_at_change`

Esse modo nao renova expiracao. Se o relacionamento ou node ja existir e nenhuma outra regra exigir recriacao, `expires_at` e preservado.

### 4. Referenciar colunas inexistentes em match

Se uma coluna referenciada em `match_attributes.columns` nao existir na linha, o relacionamento nao consegue localizar o lado correspondente corretamente.

### 5. Esperar criacao automatica de nodes a partir de relacionamento

Relacionamentos dependem de `source` e `target` ja resolvidos. Se nenhum node for encontrado em um dos lados, o relacionamento e ignorado.

## Exemplo de configuracao recomendada

```yaml
runtime:
  default_interval_seconds: 300
  sleep_seconds: 0
  dry_run: false

jobs:
  - name: failed_signins
    query: |
      SigninLogs
      | where TimeGenerated > ago(15m)
      | summarize FailedAttempts = count(), LastFailure = max(TimeGenerated) by UserPrincipalName, IPAddress, AppDisplayName
    interval_seconds: 300
    nodes:
      - types:
          - User
        template_hashes:
          - user-v1
        update_policy: merge
        expiration_time_min: 60
        static_properties:
          source_system: adx
          category: identity
        column_properties:
          name: UserPrincipalName
          last_failure_at: LastFailure
        conditional_properties:
          - type: static
            name: risk
            value: high
            conditions:
              - type: number
                column: FailedAttempts
                greater_than: 5
        property_transforms:
          - property: name
            process:
              - type: TO_LOWER

      - type: IPAddress
        template_hashes:
          - ip-address-v1
        update_policy: merge
        static_properties:
          source_system: adx
        column_properties:
          name: IPAddress

    relationships:
      - type: AUTHENTICATED_FROM
        template_hash: user-authenticated-from-ip-v1
        update_policy: merge
        expiration_time_min: 15
        static_properties:
          source_system: adx
        column_properties:
          app_display_name: AppDisplayName
          failed_attempts: FailedAttempts
        property_transforms:
          - property: app_display_name
            process:
              - type: TO_UPPER
        source:
          type: User
          match_attributes:
            columns:
              name: UserPrincipalName
        target:
          type: IPAddress
          match_attributes:
            columns:
              name: IPAddress
```

## Arquivos de exemplo no repositorio

- [config.demo.yaml](../config.demo.yaml)
- [config.test.yaml](../config.test.yaml)
