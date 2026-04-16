# Arquitetura

## Visão geral

O coletor é dividido em quatro blocos principais:

1. `config.py`
   Carrega `.env`, parseia o YAML, aplica defaults e valida o contrato.
2. `adx_client.py`
   Executa queries no ADX usando o SDK oficial e autenticação Azure.
3. `templating.py`
   Resolve propriedades, condições e gera mutações determinísticas de nodes e relacionamentos por linha.
4. `neo4j_client.py`
   Localiza entidades equivalentes e aplica `create`, `merge` ou `merge_at_change`.

## Fluxo de execução

1. O processo carrega o `.env`.
2. O YAML é lido e validado antes do scheduler iniciar.
3. Cada job roda no intervalo configurado.
4. Cada linha retornada pela query vira um `RowContext`.
5. Templates de node são avaliados.
6. Templates de relacionamento são avaliados.
7. O repositório Neo4j decide se cria, atualiza ou ignora cada mutação.

## Identidade estável

- Nodes usam `node_uid` gerado por `uuid5`.
- Relacionamentos usam `rel_uid` gerado por `uuid5`.
- O namespace do UUID é configurável por `APP_UUID_NAMESPACE`.

O uso de `uuid5` garante estabilidade para a mesma combinação de entrada, sem depender de estado local ou banco auxiliar.

## Critérios de equivalência

### Nodes

Para localizar um node já existente, o coletor procura por:

- `name` igual
- interseção de `template_hashes`

Como fallback adicional, também considera um node equivalente quando ele já possui todos os labels configurados para o template.

### Relacionamentos

Para localizar relacionamento equivalente, o coletor usa:

- source e target já encontrados no grafo
- mesmo `template_hash` ou mesmo tipo técnico

## Política de atualização

### `create`

Cria apenas quando ainda não existe equivalente.

### `merge`

Cria quando não existe. Quando existe, atualiza propriedades de negócio e `updated_at`.

### `merge_at_change`

Só atualiza quando houve mudança nas propriedades definidas pelo YAML, ou quando o template precisa acrescentar labels ou hashes ausentes.

## Regras automáticas aplicadas

### Nodes

- label base `Entity`
- `node_uid`
- `origin = "auto"`
- `template_hashes`
- `created_at`
- `updated_at`

### Relacionamentos

- `rel_uid`
- `origin = "auto"`
- `template_hashes`
- `created_at`
- `updated_at`

## Observações de consistência

- relacionamento nunca cria nodes
- se source ou target não existirem, o relacionamento é ignorado
- `updated_at` é renovado sempre que houver criação ou atualização efetiva
- para manter compatibilidade com o documento de ingestão, nodes usam `node_uid` e relacionamentos usam `rel_uid`
