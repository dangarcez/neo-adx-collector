# Arquitetura do Sistema

## Objetivo

O `neo_collector_adx` é um coletor Python configurável por YAML que:

1. carrega parâmetros operacionais e credenciais via `.env`
2. carrega regras de coleta, transformação e persistência via YAML
3. executa queries no Azure Data Explorer
4. transforma cada linha retornada em mutações de nodes e relacionamentos
5. persiste essas mutações no Neo4j de forma idempotente

O projeto prioriza previsibilidade operacional: a configuração é validada no startup, cada linha é processada isoladamente, e as decisões de criação ou atualização ficam centralizadas no repositório Neo4j.

## Contrato Canônico

Alguns nomes do YAML são diferentes dos nomes persistidos no grafo. Internamente, o contrato canônico é:

- nodes configuram `template_hashes` e persistem `z4j_template_hashes`
- relacionamentos configuram `template_hash` e persistem `z4j_template_hash`
- todo node recebe a label base `Entity`
- nodes recebem `z4j_node_uid`; relacionamentos recebem `z4j_rel_uid`
- `z4j_origin` é gravado como `"auto"` quando criado pelo coletor
- `z4j_created_at`, `z4j_updated_at` e `z4j_expires_at` usam ISO 8601 UTC
- `update_policy` aceita `create`, `merge` e `merge_at_change`, com `create` como default
- `type` em node é normalizado para `types`
- `template_hashes` em relacionamento é aceito apenas como alias de um único `template_hash`
- `dynamic_properties` é aceito como alias legado de `column_properties`
- `property_transforms` atua sobre propriedades já resolvidas de nodes e relacionamentos
- `prior_transform` em `source` e `target` atua sobre valores de colunas antes do match do relacionamento
- campos persistidos pelo app usam prefixo `z4j_`; propriedades de negócio do usuário não recebem prefixo automaticamente

## Visão de Alto Nível

```text
+-------------------+       +---------------------+       +------------------+
| .env              |       | config.yaml         |       | Azure Data       |
| runtime/segredos  |       | jobs/regras         |       | Explorer         |
+---------+---------+       +----------+----------+       +---------+--------+
          |                            |                            |
          +------------+---------------+                            |
                       v                                            |
              +----------------+                                    |
              | CLI/bootstrap  |                                    |
              | load+validate  |                                    |
              +-------+--------+                                    |
                      |                                             |
                      v                                             |
              +----------------+      agenda por job                |
              | JobScheduler   +------------------------------------+
              +-------+--------+
                      |
                      v
              +----------------+      linhas ADX
              | ADXQueryClient +-------------------+
              +-------+--------+                   |
                      |                            v
                      |                    +---------------+
                      |                    | MutationBuilder|
                      |                    | conditions     |
                      |                    | properties     |
                      |                    | transforms     |
                      |                    | stable keys    |
                      |                    +-------+-------+
                      |                            |
                      |                            v
                      |                    +---------------+
                      +------------------->| GraphRepository|
                                           | match/upsert   |
                                           +-------+-------+
                                                   |
                                                   v
                                                +-------+
                                                | Neo4j |
                                                +-------+
```

## Estrutura Principal

```text
src/neo_collector_adx/
├── cli.py              entrada de linha de comando
├── dotenv.py           carregamento simples de .env
├── config.py           parser, defaults e validação do YAML
├── app.py              orquestra bootstrap, jobs e processamento por linha
├── scheduler.py        execução uma vez ou em loop por intervalo
├── adx_client.py       cliente Azure Data Explorer
├── templating.py       engine de regras e geração de mutações
├── neo4j_client.py     repositório Neo4j e dry-run
├── models.py           dataclasses do contrato interno
├── graph_fields.py     nomes canônicos dos campos z4j_
└── logging_utils.py    configuração de logs text/json
```

## Papel das Camadas

### `cli.py`

Ponto de entrada. Carrega `.env`, aplica override de `--config`, configura logs, valida configuração quando `--validate-config` é usado, inicializa a aplicação e escolhe entre `--once` ou loop contínuo.

### `config.py`

Responsável por transformar YAML em modelo interno estável:

- aplica defaults de runtime e policies
- valida campos obrigatórios antes do loop iniciar
- normaliza aliases como `type`, `dynamic_properties`, `template_hashes` e `mergeAtChange`
- valida `property_transforms` e `prior_transform`
- valida regex, grupos de captura e referências como `$1`
- falha cedo com `ConfigurationError` quando o contrato é inválido

### `app.py`

Orquestra a execução:

- cria `MutationBuilder` com o namespace UUID configurado
- cria `ADXQueryClient`
- escolhe `DryRunGraphRepository` ou `Neo4jGraphRepository`
- processa cada job e cada linha retornada
- persiste nodes antes de relacionamentos
- registra estatísticas de linhas, nodes e relacionamentos

### `scheduler.py`

Executa jobs uma vez ou continuamente. No modo contínuo, cada job roda quando seu `interval_seconds` vence. A execução atual é sequencial e simples, adequada para previsibilidade e para evitar sobreposição acidental de escrita no Neo4j.

### `adx_client.py`

Encapsula o SDK oficial do Azure Data Explorer. O modo de autenticação vem do `.env` e pode usar `default`, `managed_identity`, `application_key` ou `az_cli`.

### `templating.py`

É o núcleo da regra de negócio. Para cada `RowContext`:

- avalia `conditions`
- resolve `static_properties`, `column_properties` e `conditional_properties`
- aplica `property_transforms`
- valida presença de `name` em nodes
- resolve `source` e `target` de relacionamentos
- aplica `prior_transform` sobre colunas de origem antes do match
- monta chaves estáveis determinísticas
- injeta campos técnicos da mutação

### `neo4j_client.py`

Centraliza persistência e equivalência no Neo4j:

- cria constraints e índices quando `NEO4J_APPLY_SCHEMA=true`
- localiza nodes equivalentes
- localiza source e target para relacionamentos
- aplica `create`, `merge` e `merge_at_change`
- gerencia `z4j_expires_at`
- preserva `z4j_created_at` e renova `z4j_updated_at` apenas quando há mutação real

## Fluxo de Execução

### 1. Inicialização

1. A CLI carrega o `.env`.
2. Variáveis já presentes no ambiente têm precedência sobre o `.env`.
3. O YAML é carregado do `--config` ou de `APP_CONFIG_PATH`.
4. `config.py` aplica defaults, normalizações e validações.
5. A aplicação cria cliente ADX e repositório Neo4j ou dry-run.
6. O scheduler executa os jobs uma vez ou em loop.

### 2. Execução de Job

1. O job envia sua query KQL ao ADX.
2. O resultado é convertido em uma lista de dicionários Python.
3. Cada linha vira um `RowContext` com `job_name` e `collected_at`.
4. A linha é processada isoladamente; erro em uma linha é logado e não aborta o job inteiro.

### 3. Processamento de Nodes

1. O builder avalia `nodes[].conditions`.
2. Resolve propriedades estáticas, de coluna e condicionais.
3. Aplica `property_transforms` na ordem declarada.
4. Ignora o node se `name` estiver ausente ou vazio.
5. Calcula `stable_key` e `z4j_node_uid`.
6. Gera `NodeMutation`.
7. O repositório decide criar, atualizar ou ignorar.

### 4. Processamento de Relacionamentos

1. O builder avalia `relationships[].conditions`.
2. Resolve propriedades do relacionamento e aplica `property_transforms`.
3. Resolve `source.match_attributes.static` sem transformação.
4. Lê as colunas de `source.match_attributes.columns`.
5. Aplica `source.prior_transform` usando o nome da coluna de origem.
6. Mapeia o valor transformado para a propriedade de match do node.
7. Repete o mesmo fluxo para `target`.
8. Calcula `stable_key` e `z4j_rel_uid`.
9. O repositório localiza source e target no Neo4j.
10. Se um dos lados não existir, o relacionamento é ignorado.
11. Se houver múltiplos source e target, o repositório cria o produto cartesiano.
12. Se houver múltiplos relacionamentos equivalentes para o mesmo par, a mutação é ignorada como inconsistência.

## Identidade Estável

IDs técnicos são gerados com UUIDv5 usando `APP_UUID_NAMESPACE`.

### Nodes

A chave estável de node usa:

- labels técnicos ordenados
- `template_hashes` ordenados
- valor final de `name`

Isso significa que transforms aplicados ao `name` fazem parte da identidade do node.

### Relacionamentos

A chave estável de relacionamento usa:

- `template_hash`
- tipo técnico do relacionamento
- tipo e atributos resolvidos do source
- tipo e atributos resolvidos do target

Os atributos de source e target entram na chave após `prior_transform`, quando configurado.

## Critérios de Equivalência

### Nodes

Um node existente é considerado equivalente quando:

- possui label base `Entity`
- tem `name` igual ao da mutação
- e possui interseção com `z4j_template_hashes`

Como fallback, o repositório também considera equivalente um node com todos os labels técnicos esperados pelo template.

### Relacionamentos

Um relacionamento existente é considerado equivalente para um par source-target quando:

- conecta exatamente os nodes source e target encontrados
- e tem o mesmo tipo técnico ou o mesmo `z4j_template_hash`

Essa regra permite atualizar relacionamentos antigos do mesmo par quando o tipo ou o hash precisarem ser normalizados.

## Transformações

### `property_transforms`

Roda sobre propriedades já resolvidas de nodes ou relacionamentos:

1. propriedades estáticas são copiadas
2. propriedades de coluna são resolvidas
3. propriedades condicionais são aplicadas
4. processors são executados na ordem declarada
5. campos automáticos `z4j_` são injetados depois

Processors suportados:

- `TO_UPPER`
- `TO_LOWER`
- `REGEX`

Valores não string ignoram processors string. Regex sem match preserva o valor original.

### `prior_transform`

Roda apenas em `source` e `target` de relacionamentos. Ele usa o mesmo schema de `property_transforms`, mas `property` referencia a coluna da query, não a propriedade do node.

Exemplo:

```yaml
source:
  type: User
  match_attributes:
    columns:
      name: UserPrincipalName
  prior_transform:
    - property: UserPrincipalName
      process:
        - type: TO_LOWER
```

Com `UserPrincipalName: "ALICE@EXAMPLE.COM"`, o match final procura `User.name = "alice@example.com"`.

`prior_transform` não altera `match_attributes.static`.

## Políticas de Atualização

### `create`

Cria apenas quando não existe entidade equivalente. Se já existir, ignora. Quando `expiration_time_min` está configurado, grava `z4j_expires_at` na criação.

### `merge`

Cria quando não existe. Quando existe, atualiza propriedades de negócio, renova `z4j_updated_at` e renova `z4j_expires_at` se `expiration_time_min` estiver configurado.

### `merge_at_change`

Cria quando não existe. Quando existe, compara apenas propriedades de negócio definidas pelo YAML, além de labels e hashes em nodes. Só atualiza quando há mudança real. Não renova `z4j_expires_at`.

## Campos Automáticos

### Nodes

- `Entity`
- labels definidos em `types`
- `z4j_node_uid`
- `z4j_origin`
- `z4j_template_hashes`
- `z4j_created_at`
- `z4j_updated_at`
- `z4j_expires_at` quando aplicável

### Relacionamentos

- tipo técnico definido em `type`
- `z4j_rel_uid`
- `z4j_origin`
- `z4j_template_hash`
- `z4j_created_at`
- `z4j_updated_at`
- `z4j_expires_at` quando aplicável

## Validação de Configuração

O startup falha antes de executar queries quando encontra erros como:

- `jobs` ausente ou vazio
- job sem `name` ou `query`
- `interval_seconds <= 0`
- node sem `type`/`types`
- node sem `template_hashes`
- node sem propriedade `name`
- relacionamento sem `type` ou `template_hash`
- `source` ou `target` sem `type`
- `source` ou `target` sem atributos de match
- `update_policy` inválido
- `expiration_time_min <= 0`
- processor de transform inválido
- regex inválida, sem grupo de captura ou com referência inexistente
- condição sem exatamente um operador

## Dry Run

Quando `runtime.dry_run=true`:

- a query ADX continua sendo executada
- templates, condições e transforms continuam sendo avaliados
- mutações são montadas normalmente
- nenhuma escrita é feita no Neo4j
- nodes e relacionamentos são contabilizados como `skipped`

Esse modo serve para validar modelagem e observar logs sem alterar o grafo.

## Observabilidade

A aplicação emite logs para:

- início e fim de job
- resumo com linhas processadas
- contadores de nodes criados, atualizados e ignorados
- contadores de relacionamentos criados, atualizados e ignorados
- falhas de job
- falhas de processamento por linha
- falhas de schema no Neo4j

O formato de log pode ser `text` ou `json`, definido por `APP_LOG_FORMAT`.

## Estratégia de Testes

A suíte atual cobre principalmente:

- carregamento e validação de YAML
- normalizações de configuração
- transforms simples e regex
- geração de mutações de node e relacionamento
- aplicação de `prior_transform` por coluna de origem
- comportamento de dry-run
- preservação de expiração em `merge_at_change`
- leitura de campos técnicos prefixados

Comando padrão:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Decisões Técnicas

### Separar `.env` de YAML

`.env` contém parâmetros operacionais e segredos. YAML contém comportamento funcional de coleta e modelagem. Isso permite promover a mesma modelagem entre ambientes sem carregar credenciais junto.

### Usar dataclasses como modelo interno

As dataclasses em `models.py` deixam o contrato explícito, reduzem dicionários soltos no núcleo e facilitam testes unitários do parser e do builder.

### Processar linhas isoladamente

Cada linha ADX vira uma unidade independente de processamento. Isso reduz blast radius de erro, simplifica condições e mantém contadores de execução previsíveis.

### Persistir nodes antes de relacionamentos

Relacionamentos não criam nodes implicitamente. A ordem garante que nodes planejados para a mesma linha sejam gravados antes da tentativa de criar arestas.

### Gerar identidade por UUIDv5

UUIDv5 mantém estabilidade entre execuções sem exigir tabela auxiliar ou estado local. A chave canônica é legível no código e deriva apenas da configuração e dos dados resolvidos.

### Centralizar persistência no repositório

O builder não conhece Cypher nem driver Neo4j. Ele apenas descreve a mutação desejada. O repositório é o único responsável por equivalência, política de update e escrita real.

## Resultado Esperado

Essa arquitetura entrega:

- configuração declarativa e validada antes da execução
- idempotência em reprocessamentos
- transforms previsíveis antes e depois da resolução de propriedades
- separação clara entre coleta, planejamento e persistência
- execução local, em container ou em dry-run
- base simples para evoluir novos processors, fontes ou regras de match
