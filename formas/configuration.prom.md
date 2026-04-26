# Referencia de Configuracao

Este documento descreve o contrato de configuracao suportado pelo coletor, cobrindo:

- variaveis de ambiente em `.env`
- estrutura e semantica do arquivo YAML
- defaults aplicados pelo bootstrap
- normalizacoes aceitas pelo parser
- regras de validacao que fazem o startup falhar

O objetivo aqui e servir como referencia operacional. Para detalhes de arquitetura, consulte [architecture.md](./architecture.md).

## Visao Geral

O coletor combina dois conjuntos de configuracao:

- `.env`: parametros operacionais, conexao, logs e runtime global
- YAML: targets Prometheus, jobs, regras de criacao de nodes e relacionamentos

Em termos praticos:

- `.env` define como a aplicacao roda
- YAML define o que a aplicacao coleta e como transforma datapoints em mutacoes no Neo4j

## Ordem de precedencia

Ao iniciar:

1. o processo carrega o arquivo `.env` informado pela flag `-env`
2. cada variavel do `.env` so e aplicada se ainda nao existir no ambiente do processo
3. o caminho do YAML vem da flag `-config` ou, se ausente, de `APP_CONFIG_PATH`
4. o YAML e normalizado e validado antes da aplicacao iniciar o loop

Isso significa que uma variavel exportada no shell tem precedencia sobre o `.env`.

## Referencia do `.env`

### Variaveis suportadas

| Variavel | Default | Obrigatoria | Descricao |
| --- | --- | --- | --- |
| `APP_CONFIG_PATH` | `configs/config.demo.yaml` | nao | Caminho padrao do arquivo YAML quando `-config` nao e informado. |
| `APP_LOG_LEVEL` | `info` | nao | Nivel de log. Valores usuais: `debug`, `info`, `warn`, `error`. |
| `APP_LOG_FORMAT` | `text` | nao | Formato de log. Suporta `text` e `json`. |
| `APP_MAX_DATAPOINT_WORKERS` | `4` | nao | Limite global de concorrencia para processar datapoints. Valores menores que `1` sao normalizados para `1`. |
| `NEO4J_URI` | `bolt://localhost:7687` | depende | URI do Neo4j. Em `dry_run` global, pode nao ser necessaria. |
| `NEO4J_DATABASE` | `neo4j` | nao | Database alvo no Neo4j. |
| `NEO4J_USERNAME` | `neo4j` | nao | Usuario de autenticacao. |
| `NEO4J_PASSWORD` | vazio | depende | Senha do Neo4j. Necessaria quando houver escrita real no banco. |
| `NEO4J_TIMEOUT_SECONDS` | `10` | nao | Timeout de conectividade com Neo4j em segundos. |
| `NEO4J_VERIFY_CONNECTIVITY` | `true` | nao | Se `true`, valida conectividade no startup. |

### Exemplo de `.env`

```dotenv
APP_CONFIG_PATH=configs/config.demo.yaml
APP_LOG_LEVEL=info
APP_LOG_FORMAT=text
APP_MAX_DATAPOINT_WORKERS=4

NEO4J_URI=bolt://localhost:7687
NEO4J_DATABASE=neo4j
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=neo4j123
NEO4J_TIMEOUT_SECONDS=10
NEO4J_VERIFY_CONNECTIVITY=true
```

### Observacoes sobre `.env`

- Linhas vazias e comentarios com `#` sao ignorados.
- O parser aceita linhas com ou sem `export`.
- Valores entre aspas simples ou duplas sao suportados.
- O arquivo `.env` nao sobrescreve variaveis ja presentes no ambiente do processo.

## Estrutura do YAML

O arquivo YAML tem uma raiz unica com `prom_targets`.

### Exemplo completo

```yaml
prom_targets:
  - name: main_prometheus
    base_url: http://localhost:9090
    timeout_seconds: 10
    verify_tls: true
    runtime:
      default_interval_seconds: 60
      sleep_seconds: 0
      dry_run: false
    jobs:
      - name: kubernetes_pods
        query: kube_pod_info
        interval_seconds: 30
        nodes:
          - types:
              - Namespace
            template_hashes:
              - namespace-v1
            update_policy: merge
            label_properties:
              name: namespace

          - type: Pod
            template_hashes:
              - pod-v1
            static_properties:
              kind: workload
              source_system: prometheus
            label_properties:
              name: pod
              namespace: namespace
              scrape_value: __value__
              scrape_timestamp: __timestamp__
            conditional_properties:
              - type: static
                name: activity
                value: high
                conditions:
                  - type: value
                    greater_than: 100
            property_transforms:
              - property: name
                process:
                  - type: TO_UPPER

        relationships:
          - type: OWNS
            template_hash: namespace-pod-v1
            update_policy: merge
            static_properties:
              source_system: prometheus
            property_transforms:
              - property: source_system
                process:
                  - type: TO_LOWER
            source:
              type: Namespace
              match_attributes:
                labels:
                  name: namespace
            target:
              type: Pod
              match_attributes:
                labels:
                  name: pod
```

## Raiz: `prom_targets`

`prom_targets` deve conter pelo menos um target.

Cada item representa um endpoint Prometheus independente, com sua propria lista de jobs.

### Campos de `prom_targets[]`

| Campo | Tipo | Obrigatorio | Default | Descricao |
| --- | --- | --- | --- | --- |
| `name` | string | sim | - | Nome logico do target. Aparece em logs e metricas. |
| `base_url` | string | sim | - | URL base do Prometheus, por exemplo `http://localhost:9090`. |
| `timeout_seconds` | inteiro | nao | `10` | Timeout HTTP usado nas queries para esse target. |
| `verify_tls` | boolean | nao | `true` | Se `false`, desabilita verificacao TLS do cliente HTTP. |
| `runtime` | objeto | nao | ver abaixo | Parametros de execucao do target. |
| `jobs` | lista | sim | - | Lista de jobs executados contra esse target. |

## Runtime do target

Bloco: `prom_targets[].runtime`

### Campos

| Campo | Tipo | Obrigatorio | Default | Descricao |
| --- | --- | --- | --- | --- |
| `default_interval_seconds` | inteiro | nao | `60` | Intervalo padrao aplicado a jobs sem `interval_seconds` explicito. |
| `sleep_seconds` | numero | nao | `0` | Pausa em segundos apos o processamento de cada datapoint. Aceita frações, como `0.25` ou `0.5`. |
| `dry_run` | boolean | nao | `false` | Se `true`, consulta o Prometheus e monta os planos, mas nao grava no Neo4j. |

### Comportamento de `dry_run`

Quando `dry_run` esta ativo:

- a query no Prometheus continua acontecendo
- as condicoes e propriedades continuam sendo resolvidas
- nodes e relacionamentos planejados sao contabilizados como `skipped`
- nenhuma escrita e feita no Neo4j

## Jobs

Bloco: `prom_targets[].jobs[]`

Cada job executa uma query Prometheus e processa cada datapoint retornado de forma isolada.

### Campos

| Campo | Tipo | Obrigatorio | Default | Descricao |
| --- | --- | --- | --- | --- |
| `name` | string | sim | - | Nome logico do job. |
| `query` | string | sim | - | Expressao PromQL executada no target. |
| `interval_seconds` | inteiro | nao | `runtime.default_interval_seconds` | Intervalo entre execucoes do job. |
| `nodes` | lista | nao | lista vazia | Templates de node avaliados para cada datapoint. |
| `relationships` | lista | nao | lista vazia | Templates de relacionamento avaliados para cada datapoint. |

### Observacoes

- `interval_seconds` precisa ser maior que zero apos a normalizacao.
- Um job pode ter apenas nodes, apenas relacionamentos, ou ambos.
- Os relacionamentos nao criam nodes implicitamente; eles so sao aplicados quando `source` e `target` encontram ao menos um node cada.

## Nodes

Bloco: `prom_targets[].jobs[].nodes[]`

Cada template de node e avaliado contra cada datapoint retornado pela query do job.

### Campos

| Campo | Tipo | Obrigatorio | Default | Descricao |
| --- | --- | --- | --- | --- |
| `type` | string | condicional | - | Alias para um unico tipo. |
| `types` | lista de string | condicional | - | Lista de labels tecnicas do node. Deve haver ao menos uma. |
| `template_hashes` | lista de string | sim | - | Lista de hashes de definicoes associados ao node. |
| `update_policy` | string | nao | `create` | Politica de persistencia: `create`, `merge` ou `merge_at_change`. |
| `expiration_time_min` | inteiro | nao | ausente | Quando informado, gera `expires_at` como horario atual UTC + esse numero de minutos. So e aplicado em `create` e `merge`. |
| `static_properties` | mapa | nao | `{}` | Propriedades literais copiadas para o node. |
| `label_properties` | mapa string->string | nao | `{}` | Propriedades dinamicas resolvidas a partir de labels ou tokens especiais. |
| `conditional_properties` | lista | nao | `[]` | Propriedades aplicadas apenas quando suas condicoes passam. |
| `property_transforms` | lista | nao | `[]` | Processamentos aplicados sobre propriedades ja resolvidas antes dos campos automaticos. |
| `conditions` | lista | nao | `[]` | Filtro para decidir se o template deve ser aplicado ao datapoint. |

### Regras obrigatorias

- deve haver pelo menos um tipo em `types`, ou um `type` que sera normalizado para `types`
- deve haver ao menos um item em `template_hashes`
- a propriedade `name` precisa ser definida em `static_properties` ou `label_properties`

### Exemplo minimo

```yaml
- type: Namespace
  template_hashes:
    - namespace-v1
  label_properties:
    name: namespace
```

### Exemplo com propriedades dinamicas e condicionais

```yaml
- types:
    - Pod
  template_hashes:
    - pod-v1
  update_policy: merge
  expiration_time_min: 30
  static_properties:
    kind: workload
  label_properties:
    name: pod
    namespace: namespace
    metric_value: __value__
    observed_at: __timestamp__
  conditional_properties:
    - type: static
      name: health
      value: critical
      conditions:
        - type: value
          greater_than: 0.9
    - type: label
      name: team
      from_label: owner_team
      conditions:
        - type: label
          label: owner_team
          not_equals: ""
  property_transforms:
    - property: name
      process:
        - type: TO_UPPER
    - property: namespace
      process:
        - type: TO_LOWER
```

## Relacionamentos

Bloco: `prom_targets[].jobs[].relationships[]`

Cada template de relacionamento e avaliado contra cada datapoint retornado pela query do job.

### Campos

| Campo | Tipo | Obrigatorio | Default | Descricao |
| --- | --- | --- | --- | --- |
| `type` | string | sim | - | Tipo tecnico do relacionamento no Neo4j. |
| `template_hash` | string | condicional | - | Hash canonico da definicao de relacionamento. |
| `template_hashes` | lista de string | condicional | - | Alias aceito apenas quando houver um unico item; sera normalizado para `template_hash`. |
| `update_policy` | string | nao | `create` | Politica de persistencia: `create`, `merge` ou `merge_at_change`. |
| `expiration_time_min` | inteiro | nao | ausente | Quando informado, gera `expires_at` como horario atual UTC + esse numero de minutos. So e aplicado em `create` e `merge`. |
| `static_properties` | mapa | nao | `{}` | Propriedades literais do relacionamento. |
| `label_properties` | mapa string->string | nao | `{}` | Propriedades dinamicas resolvidas do datapoint. |
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
- type: OWNS
  template_hash: namespace-pod-v1
  source:
    type: Namespace
    match_attributes:
      labels:
        name: namespace
  target:
    type: Pod
    match_attributes:
      labels:
        name: pod
```

### Observacao importante sobre persistencia

Na configuracao, relacionamento usa `template_hash` singular como entrada canonica. Durante a construcao da mutacao para o grafo, esse valor e persistido como `template_hashes` com um unico item.

Exemplo:

- config: `template_hash: namespace-pod-v1`
- propriedade persistida no Neo4j: `template_hashes: ["namespace-pod-v1"]`

## Source e Target

Blocos:

- `prom_targets[].jobs[].relationships[].source`
- `prom_targets[].jobs[].relationships[].target`

Esses blocos definem como localizar os nodes ja existentes que receberao o relacionamento.

### Campos

| Campo | Tipo | Obrigatorio | Descricao |
| --- | --- | --- | --- |
| `type` | string | sim | Label tecnica do node a ser encontrado. |
| `match_attributes.static` | mapa | nao | Atributos fixos usados no match. |
| `match_attributes.labels` | mapa string->string | nao | Atributos resolvidos a partir de labels do datapoint. |

Pelo menos um atributo de match deve existir entre `static` e `labels`.

Comportamento de match:

- se nenhum `source` ou nenhum `target` for encontrado, o relacionamento e ignorado
- se houver multiplos `source` e multiplos `target`, o coletor cria o produto cartesiano entre eles
- exemplo: `2 source x 3 target = 6` relacionamentos
- a unica ambiguidade que continua sendo erro e encontrar mais de um relacionamento equivalente ja existente para o mesmo par `source-target`

### Exemplo

```yaml
source:
  type: Namespace
  match_attributes:
    static:
      origin: auto
    labels:
      name: namespace
```

### Aliases legados suportados

Ainda sao aceitos os seguintes aliases:

- `match_label_attributes`
- `match_static_attributes`

Eles sao incorporados internamente em `match_attributes`.

Exemplo:

```yaml
target:
  type: Pod
  match_label_attributes:
    name: pod
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
  kind: workload
  source_system: prometheus
```

### `label_properties`

Mapa de `nome_da_propriedade -> token_de_origem`.

O token pode ser:

- o nome de uma label do datapoint, como `namespace` ou `job`
- `__value__` para o valor numerico da serie
- `__timestamp__` para o timestamp em RFC3339 UTC

Exemplo:

```yaml
label_properties:
  name: pod
  namespace: namespace
  metric_value: __value__
  observed_at: __timestamp__
```

Se o token apontar para uma label ausente no datapoint:

- em `label_properties`, a propriedade e simplesmente omitida
- `__value__` e `__timestamp__` continuam obrigatoriamente resolviveis
- em `conditional_properties` do tipo `label`, a propriedade tambem e omitida
- essa tolerancia nao se aplica a `match_attributes.labels` de `source` e `target`

### `conditional_properties`

Cada item define uma propriedade que so sera aplicada se todas as condicoes do bloco forem verdadeiras.

Campos:

| Campo | Tipo | Obrigatorio | Descricao |
| --- | --- | --- | --- |
| `type` | string | sim | `static` ou `label`. |
| `name` | string | sim | Nome da propriedade a ser escrita. |
| `value` | qualquer | condicional | Obrigatorio quando `type: static`. |
| `from_label` | string | condicional | Obrigatorio quando `type: label`. |
| `conditions` | lista | sim | Lista de condicoes que habilitam a propriedade. |

Exemplos:

```yaml
conditional_properties:
  - type: static
    name: severity
    value: high
    conditions:
      - type: value
        greater_than: 0.8
```

```yaml
conditional_properties:
  - type: label
    name: team
    from_label: owner_team
    conditions:
      - type: label
        label: owner_team
        not_equals: ""
```

### `property_transforms`

Cada item aponta para uma propriedade ja resolvida e aplica uma lista ordenada de processors sobre o valor atual.

Campos:

| Campo | Tipo | Obrigatorio | Descricao |
| --- | --- | --- | --- |
| `property` | string | sim | Nome da propriedade que sera processada. |
| `process` | lista | sim | Lista ordenada de processors aplicados em sequencia. |

Cada item de `process` e um objeto com:

| Campo | Tipo | Obrigatorio | Descricao |
| --- | --- | --- | --- |
| `type` | string | sim | Processor a aplicar. Na versao atual: `TO_UPPER` ou `TO_LOWER`. |

Exemplo em node:

```yaml
property_transforms:
  - property: name
    process:
      - type: TO_UPPER
  - property: environment
    process:
      - type: TO_LOWER
```

Exemplo em relacionamento:

```yaml
property_transforms:
  - property: source_system
    process:
      - type: TO_LOWER
```

Comportamento:

- o bloco roda depois de `static_properties`, `label_properties` e `conditional_properties`
- o bloco roda antes da injecao de `node_uid`, `rel_uid`, `template_hashes`, `origin`, `created_at`, `updated_at` e `expires_at`
- se a propriedade alvo nao existir no mapa final, o item e ignorado
- se o valor existir mas nao for `string`, `TO_UPPER` e `TO_LOWER` sao ignorados para aquela propriedade
- em node, transformar `name` muda o `name` final e o `node_uid` derivado dele
- em relacionamento, `property_transforms` afeta apenas `properties`; `source` e `target` nao participam desse pipeline
- a ordem da lista `process` importa; a saida de um processor vira a entrada do proximo

### `expiration_time_min`

Campo opcional em:

- `nodes[]`
- `relationships[]`

Quando informado, o repositorio calcula automaticamente:

- `expires_at = horario_atual_utc + expiration_time_min`

Formato persistido:

- `expires_at` em RFC3339 UTC

Regras:

- `expiration_time_min` deve ser maior que zero quando informado
- `expires_at` nao e criado pelo planner; ele e injetado no momento da persistencia
- `expires_at` e aplicado em policies `create` e `merge`
- `expires_at` nao e criado nem renovado quando a template usa `merge_at_change`
- `expires_at` e tratado como campo automatico, no mesmo grupo de `created_at` e `updated_at`

Exemplo:

```yaml
- type: Host
  template_hashes:
    - host-v1
  update_policy: merge
  expiration_time_min: 30
  label_properties:
    name: machine_name
```

### Ordem de aplicacao

As propriedades sao resolvidas nesta ordem:

1. `static_properties`
2. `label_properties`
3. `conditional_properties`
4. `property_transforms`

Se a mesma chave aparecer mais de uma vez, a ultima atribuicao vence.

Depois disso, na camada de persistencia, a aplicacao ainda pode injetar campos automaticos como `node_uid`, `rel_uid`, `origin`, `created_at`, `updated_at` e `expires_at`.

## Condicoes

Condicoes podem ser usadas em:

- `nodes[].conditions`
- `relationships[].conditions`
- `conditional_properties[].conditions`

Todas as condicoes de uma lista precisam passar. O comportamento e um `AND` implicito.

### Condicoes de label

Campos suportados:

| Campo | Obrigatorio | Observacao |
| --- | --- | --- |
| `type: label` | sim | Identifica o tipo da condicao. |
| `label` | sim | Nome da label no datapoint. |
| `equals` ou `not_equals` | exatamente um | Somente um operador pode ser usado. |

Exemplo:

```yaml
- type: label
  label: namespace
  equals: production
```

Se a label nao existir no datapoint, a condicao resulta em `false`.

### Condicoes de existencia de label

Campos suportados:

| Campo | Obrigatorio | Observacao |
| --- | --- | --- |
| `type: label_exists` | sim | Verifica se a label existe no datapoint. |
| `label` | sim | Nome da label que deve existir. |

Esse tipo nao aceita operadores como `equals`, `not_equals`, `greater_than` ou `less_than`.

Exemplo:

```yaml
- type: label_exists
  label: namespace
```

Se a label existir no datapoint, a condicao resulta em `true`. Caso contrario, resulta em `false`.

### Condicoes de value

Campos suportados:

| Campo | Obrigatorio | Observacao |
| --- | --- | --- |
| `type: value` | sim | Identifica o tipo da condicao. |
| `equals`, `not_equals`, `greater_than` ou `less_than` | exatamente um | Somente um operador pode ser usado. |

Exemplo:

```yaml
- type: value
  greater_than: 100
```

Comparacoes numericas aceitam inteiros, floats e strings numericas nos operadores `equals` e `not_equals`.

## Tokens especiais

Os tokens reservados abaixo podem ser usados em `label_properties`, `from_label` e `match_attributes.labels`:

| Token | Resultado |
| --- | --- |
| `__value__` | Valor numerico do datapoint. |
| `__timestamp__` | Timestamp do datapoint em RFC3339 UTC. |

Qualquer outro token e interpretado como nome de label do datapoint.

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
- se `expiration_time_min` estiver configurado, cria `expires_at` no momento da insercao

`merge`

- cria quando nao existe
- atualiza propriedades quando ja existe
- se `expiration_time_min` estiver configurado, cria ou renova `expires_at`

`merge_at_change`

- cria quando nao existe
- compara apenas os atributos definidos no YAML, ignorando campos automaticos
- se nada mudou nos atributos de negocio, nao atualiza a entidade
- se algo mudou, atualiza propriedades e renova `updated_at`
- mesmo quando `expiration_time_min` estiver configurado, `expires_at` nao e criado nem renovado nessa policy

Se o campo for omitido, o default e `create`.

## Defaults e normalizacoes

### Defaults aplicados

- `prom_targets[].timeout_seconds`: `10`
- `prom_targets[].verify_tls`: `true`
- `prom_targets[].runtime.default_interval_seconds`: `60`
- `prom_targets[].runtime.sleep_seconds`: `0`
- `prom_targets[].runtime.dry_run`: `false`
- `jobs[].interval_seconds`: herda `runtime.default_interval_seconds`
- `nodes[].update_policy`: `create`
- `relationships[].update_policy`: `create`
- mapas de propriedades ausentes sao normalizados para objetos vazios
- `expiration_time_min` ausente nao gera `expires_at`

### Normalizacoes aceitas

- `nodes[].type` e convertido para `nodes[].types` com um item
- `relationships[].template_hashes` com um unico item e convertido para `template_hash`
- `mergeAtChange` e `merge-at-change` sao aceitos como alias de `merge_at_change`
- `property_transforms[].process[].type` e normalizado para caixa alta antes da validacao
- aliases legados de match sao incorporados em `match_attributes`
- strings com espacos nas extremidades sao `trimadas` nos campos relevantes

## Regras de validacao que falham no startup

O bootstrap falha antes de iniciar o scheduler quando encontrar erros como:

- `prom_targets` vazio
- target sem `name`
- target sem `base_url`
- target sem jobs
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
- condicoes com mais de um operador
- `conditional_properties` sem `name`, `type` valido ou `conditions`
- `property_transforms` sem `property`, sem `process` ou com `process[].type` nao suportado
- `expiration_time_min <= 0` quando informado

## Erros comuns de modelagem

### 1. Esquecer `name` em nodes

Sem `name`, o node e rejeitado na validacao. O campo pode vir de:

- `static_properties.name`
- `label_properties.name`

### 2. Usar `template_hashes` com varios itens em relacionamento

O parser aceita `template_hashes` apenas como alias para um unico valor. Para relacionamento, o campo de entrada canonico continua sendo `template_hash`.

### 3. Referenciar labels inexistentes

Se um token em `label_properties` ou em `conditional_properties` do tipo `label` apontar para uma label ausente no datapoint, a propriedade e omitida. Ja em `match_attributes.labels`, a ausencia continua sendo erro de processamento.

### 4. Esperar transformacao sobre campo automatico

`property_transforms` roda antes da injecao dos campos automaticos. Isso significa que ele pode atuar sobre propriedades declaradas no YAML, inclusive `name`, mas nao sobre `node_uid`, `rel_uid`, `template_hashes`, `origin`, `created_at`, `updated_at` ou `expires_at`.

### 5. Esperar criacao automatica de nodes a partir de relacionamento

Relacionamentos dependem de `source` e `target` ja resolvidos. Se nenhum node for encontrado em um dos lados, o relacionamento e ignorado. Se houver multiplos matches em `source` ou `target`, o coletor cria todos os relacionamentos possiveis entre os pares encontrados.

## Exemplo de configuracao recomendada

```yaml
prom_targets:
  - name: prometheus_up_demo
    base_url: http://host.docker.internal:8085
    timeout_seconds: 10
    verify_tls: true
    runtime:
      default_interval_seconds: 45
      sleep_seconds: 1
      dry_run: false
    jobs:
      - name: prometheus_build
        query: up
        interval_seconds: 30
        nodes:
          - types:
              - PrometheusJob
            template_hashes:
              - prometheus-job-v1
            update_policy: merge
            label_properties:
              name: job
              job: job

          - type: PrometheusTarget
            template_hashes:
              - prometheus-target-v1
            static_properties:
              kind: scrape_target
            label_properties:
              name: instance
              job: job
              instance: instance
              status: __value__

        relationships:
          - type: SCRAPES
            template_hash: job-scrapes-target-v1
            update_policy: merge
            source:
              type: PrometheusJob
              match_attributes:
                labels:
                  name: job
            target:
              type: PrometheusTarget
              match_attributes:
                labels:
                  name: instance
```

## Arquivos de exemplo no repositorio

- [configs/config.demo.yaml](../configs/config.demo.yaml)
- [configs/config.test.yaml](../configs/config.test.yaml)
- [configs/config.compose.yaml](../configs/config.compose.yaml)
