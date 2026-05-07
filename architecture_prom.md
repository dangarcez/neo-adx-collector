# Arquitetura do Sistema

## Objetivo

Construir um coletor em Go, executável localmente e em container, que:

1. leia configurações de infraestrutura via `.env`
2. leia regras de coleta e transformação via YAML
3. execute queries periódicas em um ou mais Prometheus
4. transforme cada datapoint em operações de criação/atualização de nodes e relacionamentos no Neo4j
5. mantenha comportamento idempotente, configurável e seguro para evolução

## Premissas e normalizações do contrato

O PRD, o guia `ingestao-automatica.md` e o `config.demo.yaml` têm pequenas inconsistências. Para a arquitetura, o contrato canônico deve ser:

- nodes configuram `template_hashes`, mas persistem `z4j_template_hashes`
- relacionamentos configuram `template_hash`, mas persistem `z4j_template_hash`
- relacionamentos usam `z4j_rel_uid`, não `z4j_node_uid`
- todo node recebe a label base `Entity`
- `z4j_origin` deve ser sempre `"auto"`
- `z4j_created_at`, `z4j_updated_at` e `z4j_expires_at` devem ser gravados em ISO 8601 UTC
- `update_policy` aceita `create`, `merge` e `merge_at_change`, com `create` como default
- a configuração YAML deve normalizar `type` e `types` para um campo interno único
- a configuração YAML pode definir `property_transforms` em nodes e relacionamentos para pós-processar propriedades resolvidas
- a configuração YAML pode definir `expiration_time_min` para pedir injecao automatica de `z4j_expires_at` na persistencia
- `z4j_` e prefixo reservado para campos gerados pelo app e nao pode ser usado em propriedades criadas pelo usuario
- typos do YAML de exemplo, como `greaterThen`, devem ser tratados como inválidos na validação

Essa decisão é necessária porque o guia de ingestão define os nomes exatos esperados pelo app principal.

## Visão de alto nível

```text
+-------------------+       +----------------------+       +------------------+
| .env              |       | config.yaml          |       | Prometheus N     |
| segredos/runtime  |       | targets/jobs/regras  |       | /api/v1/query    |
+---------+---------+       +----------+-----------+       +---------+--------+
          |                            |                             |
          +------------+---------------+                             |
                       v                                             |
               +---------------+                                     |
               | Bootstrap     |                                     |
               | load+validate |                                     |
               +-------+-------+                                     |
                       |                                             |
                       v                                             |
               +---------------+     agenda por job                  |
               | Scheduler     +-------------------------------------+
               +-------+-------+
                       |
                       v
               +---------------+     datapoints
               | Collector     +-------------------+
               | Prometheus    |                   |
               +-------+-------+                   v
                       |                    +-------------+
                       |                    | Rule Engine |
                       |                    | conditions  |
                       |                    | properties  |
                       |                    | transforms  |
                       |                    | ids         |
                       |                    +------+------+ 
                       |                           |
                       |                           v
                       |                    +-------------+
                       +------------------->| Neo4j Repo  |
                                            | upsert/match|
                                            +------+------+ 
                                                   |
                                                   v
                                             +-----------+
                                             | Neo4j     |
                                             +-----------+
```

## Estrutura de pastas

```text
neo_collector_go/
├── cmd/
│   └── neo-collector/
│       └── main.go
├── configs/
│   ├── config.demo.yaml
│   └── config.schema.yaml
├── docs/
│   ├── architecture.md
│   ├── build-and-run.md
│   └── config-reference.md
├── internal/
│   ├── app/
│   │   ├── bootstrap.go
│   │   └── runtime.go
│   ├── config/
│   │   ├── env.go
│   │   ├── yaml.go
│   │   ├── model.go
│   │   └── validate.go
│   ├── domain/
│   │   ├── datapoint.go
│   │   ├── graph.go
│   │   ├── policy.go
│   │   └── errors.go
│   ├── scheduler/
│   │   ├── scheduler.go
│   │   └── worker_pool.go
│   ├── collector/
│   │   └── prometheus/
│   │       ├── client.go
│   │       └── query.go
│   ├── engine/
│   │   ├── processor.go
│   │   ├── conditions.go
│   │   ├── properties.go
│   │   ├── property_transforms.go
│   │   ├── identity.go
│   │   └── planner.go
│   ├── repository/
│   │   └── neo4j/
│   │       ├── client.go
│   │       ├── nodes.go
│   │       ├── relationships.go
│   │       └── tx.go
│   ├── observability/
│   │   ├── logger.go
│   │   └── metrics.go
│   └── version/
│       └── version.go
├── tests/
│   ├── integration/
│   └── fixtures/
├── .env.example
├── Dockerfile
├── Makefile
├── README.md
└── go.mod
```

## Papel de cada camada

### `cmd/neo-collector`

Ponto de entrada. Inicializa logger, carrega configuração, sobe scheduler e trata shutdown gracioso.

### `internal/config`

Responsável por:

- carregar `.env` para segredos e parâmetros de ambiente
- carregar YAML com targets, jobs, nodes e relacionamentos
- validar regras obrigatórias antes da aplicação subir
- normalizar diferenças de schema do YAML para um modelo interno estável
- normalizar `property_transforms` para tipos canônicos de processor

### `internal/scheduler`

Responsável por executar cada job no intervalo definido. O scheduler cria uma rotina por job e impede sobreposição de execuções do mesmo job.

### `internal/collector/prometheus`

Encapsula comunicação com Prometheus, inclusive timeout, TLS, tratamento de erro e conversão da resposta em `Datapoint`.

### `internal/engine`

É o núcleo da regra de negócio. Para cada datapoint:

- avalia condições de criação
- resolve propriedades estáticas, dinâmicas e condicionais
- aplica `property_transforms` sobre as propriedades resolvidas
- carrega metadados de persistencia como `expiration_time_min`
- garante presença de `name` em nodes
- calcula identidades estáveis
- gera um plano de mutação para Neo4j

### `internal/repository/neo4j`

Executa as operações de persistência com transação, obedecendo as regras:

- node primeiro
- relacionamento depois
- relacionamento nunca cria node
- `merge` sempre atualiza quando a entidade já existe
- `merge_at_change` só atualiza quando algum atributo de negócio vindo do YAML mudou
- `create` só insere se não existir
- `z4j_updated_at` só muda quando há mutação real
- `z4j_expires_at` só é criado ou renovado quando `expiration_time_min` estiver configurado e a policy for `create` ou `merge`

### `internal/observability`

Centraliza logs estruturados, métricas operacionais e eventualmente health checks.

## Fluxo de dados

### 1. Inicialização

1. A aplicação lê `.env`.
2. Lê o YAML de configuração.
3. Valida a configuração inteira antes de iniciar o loop.
4. Cria clientes de Prometheus e Neo4j.
5. Registra um scheduler para cada combinação `target + job`.

### 2. Execução de job

1. O scheduler dispara um job pelo intervalo configurado.
2. O coletor chama o endpoint `/api/v1/query` do target Prometheus.
3. A resposta é convertida em uma lista de datapoints padronizados, contendo labels, value e timestamp.
4. Cada datapoint é processado isoladamente.

### 3. Processamento de datapoint

1. O engine avalia as condições dos nodes.
2. Para cada node elegível, resolve `static_properties`, `label_properties` e `conditional_properties`.
3. Aplica `property_transforms` sobre o mapa final de propriedades resolvidas.
4. Carrega metadados de persistencia como `expiration_time_min`.
5. Injeta campos automáticos, calcula `z4j_node_uid` e gera o plano de persistência.
6. O engine avalia as condições dos relacionamentos.
7. Para cada relacionamento elegível, resolve propriedades, aplica `property_transforms`, carrega `expiration_time_min`, resolve selectors aplicando `prior_transform` sobre tokens de origem quando configurado e gera o plano de persistência sem criar nodes.

### 4. Persistência no Neo4j

1. Os nodes do datapoint são persistidos primeiro.
2. O repositório verifica existência por identidade de negócio.
3. Aplica `create`, `merge` ou `merge_at_change`.
4. Injeta `z4j_created_at`, `z4j_updated_at` e, quando aplicavel, `z4j_expires_at`.
5. Os relacionamentos são persistidos em seguida.
6. Source e target são localizados por tipo e atributos definidos na configuração.
7. Se um dos lados não existir, o relacionamento é ignorado.
8. Se houver múltiplos matches em source ou target, o repositório cria o produto cartesiano entre eles.
9. Se já existirem múltiplos relacionamentos equivalentes para o mesmo par source-target, a aplicação falha aquele datapoint por inconsistência de identidade.

### 5. Observabilidade

Para cada execução, a aplicação registra:

- target
- job
- duração
- quantidade de datapoints
- quantidade de nodes criados ou atualizados
- quantidade de relacionamentos criados ou atualizados
- erros por fase

## Decisões técnicas justificadas

### 1. Separar `.env` de YAML

`.env` fica com segredos e parâmetros operacionais. YAML fica com comportamento de coleta e mapeamento. Isso evita misturar credenciais com regra de negócio e permite promover a mesma configuração funcional entre ambientes.

### 2. Usar `cmd` + `internal`

Essa é a convenção idiomática em Go para encapsular regras internas e manter o ponto de entrada mínimo. Melhora manutenção e reduz acoplamento acidental.

### 3. Usar scheduler por intervalo, não cron

O PRD define intervalos em segundos por job, não janelas de calendário. `time.Ticker` com controle de sobreposição é mais simples, previsível e suficiente para esse caso.

### 4. Processar datapoints isoladamente

O PRD exige que a lógica seja aplicada separadamente para cada resultado da query. Isso simplifica regras condicionais, rastreabilidade de erro e idempotência.

### 5. Gerar IDs estáveis com UUID determinístico

`z4j_node_uid` e `z4j_rel_uid` devem ser estáveis. A melhor abordagem é UUIDv5 sobre uma chave canônica:

- node: `sorted(types) + name + sorted(template_hashes)`
- relacionamento: `source_identity + rel_type + target_identity + template_hash`

Isso evita duplicação entre execuções e mantém a exigência de estabilidade do contrato.

### 6. Persistir primeiro nodes, depois relacionamentos

O PRD determina que relacionamentos não criem nodes. Logo, a ordem de persistência precisa respeitar dependência explícita.

### 7. Isolar `property_transforms` em um registry de processors

Os processors de propriedades entram como um pipeline dedicado entre a resolução de propriedades e a injeção dos campos automáticos. Essa separação evita espalhar `switch` pelo planner, mantém a semântica de `ResolveProperties` explícita e facilita adicionar novos processors no futuro sem redesenhar o contrato do YAML.

### 8. Usar políticas explícitas de persistência

Para `create`, o repositório faz verificação prévia e não altera registros existentes. Para `merge`, faz upsert e sempre renova `z4j_updated_at` quando a entidade já existe. Para `merge_at_change`, compara apenas os atributos vindos do YAML e só atualiza quando houver mudança real de negócio. Isso evita churn artificial de `z4j_updated_at` sem perder o comportamento antigo de `merge`. `z4j_expires_at` segue a mesma linha de automação por policy: só entra nas policies `create` e `merge`, mesmo quando `expiration_time_min` estiver configurado.

### 9. Validar configuração no startup

Erros como ausência de `name`, uso de operador inválido, `template_hash` ausente na config, ou source ou target incompletos devem falhar antes da aplicação entrar em loop. Isso reduz falhas silenciosas em produção.

### 10. Usar drivers oficiais

- Prometheus: cliente HTTP oficial do ecossistema Prometheus
- Neo4j: `neo4j-go-driver/v5`

A decisão reduz risco de incompatibilidade, simplifica manutenção e melhora suporte a autenticação, timeout e retry.

### 11. Logs estruturados com `log/slog`

`slog` já faz parte da stdlib moderna do Go. Evita dependência desnecessária e facilita emitir logs JSON para container e agregadores.

### 12. Concorrência com limite

Cada job pode rodar em sua própria goroutine, mas o processamento de datapoints deve passar por pool limitado. Isso mantém escalabilidade sem saturar Neo4j ou Prometheus quando houver queries volumosas.

### 13. Tratamento explícito de ambiguidades de identidade

Múltiplos matches em source e target são tratados como fan-out controlado, gerando todos os pares possíveis. A ambiguidade que continua sendo erro é encontrar mais de um relacionamento equivalente para o mesmo par source-target e mesmo `z4j_template_hash`, porque isso indica identidade inconsistente no grafo.

Nao ha compatibilidade operacional com relacionamentos gravados nos nomes antigos sem prefixo.

## Contrato interno sugerido

### Modelo canônico de node

```text
NodeTemplate
- Types []string
- TemplateHashes []string
- UpdatePolicy create|merge|merge_at_change
- ExpirationTimeMin *int
- StaticProperties map[string]any
- DynamicProperties []DynamicProperty
- ConditionalProperties []ConditionalProperty
- PropertyTransforms []PropertyTransform
- Conditions []Condition
```

### Modelo canônico de relacionamento

```text
RelationshipTemplate
- Type string
- TemplateHash string
- UpdatePolicy create|merge|merge_at_change
- ExpirationTimeMin *int
- Source NodeSelector
- Target NodeSelector
- StaticProperties map[string]any
- DynamicProperties []DynamicProperty
- ConditionalProperties []ConditionalProperty
- PropertyTransforms []PropertyTransform
- Conditions []Condition
```

## Estratégia de persistência no Neo4j

### Nodes

Critério de existência:

- label base `Entity`
- labels definidos em `Types`
- `name`

Campos automáticos:

- `z4j_node_uid`
- `z4j_template_hashes`
- `z4j_origin`
- `z4j_created_at`
- `z4j_updated_at`
- `z4j_expires_at` quando `expiration_time_min` estiver configurado e a policy for `create` ou `merge`

### Relacionamentos

Critério de existência:

- `source`
- `target`
- tipo do relacionamento
- `z4j_template_hash`

Campos automáticos:

- `z4j_rel_uid`
- `z4j_template_hash`
- `z4j_origin`
- `z4j_created_at`
- `z4j_updated_at`
- `z4j_expires_at` quando `expiration_time_min` estiver configurado e a policy for `create` ou `merge`

## Regras de validação obrigatórias

- cada job deve ter `name`, `query` e `interval_seconds > 0`
- cada node deve ter pelo menos um tipo
- cada node deve expor `name` em propriedade estática ou dinâmica
- cada node deve ter ao menos um `template_hashes`
- cada relacionamento deve ter `type`, `template_hash` (na config), `source` e `target`
- propriedades criadas pelo usuario nao podem usar o prefixo reservado `z4j_`
- source e target devem ter `type` e pelo menos um atributo de match
- tipos válidos de condição por label: `label` com `equals` ou `not_equals`, e `label_exists`
- operadores válidos para value: `equals`, `not_equals`, `greater_than`, `less_than`
- aliases internos como `__value__` e `__timestamp__` são reservados

## Estratégia de testes

- testes unitários para validação de config
- testes unitários para avaliação de condições e resolução de propriedades
- testes unitários para geração de identidade estável
- testes de integração com Neo4j e Prometheus em containers
- testes de contrato para exemplos de YAML em `tests/fixtures`

## Resultado esperado

Essa arquitetura entrega:

- modularidade para crescer sem virar script monolítico
- segurança de contrato com o app principal
- idempotência nas reexecuções
- suporte a múltiplos targets e múltiplos jobs
- clareza para operação local e em container
