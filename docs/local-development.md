# Desenvolvimento Local

## Setup rápido

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
cp .env.example .env
```

Ajuste o `.env` com as credenciais reais do ADX e do Neo4j.

## Fluxo recomendado

1. Validar o YAML:

```bash
python3 -m neo_collector_adx --env .env --config config.demo.yaml --validate-config
```

2. Rodar uma vez:

```bash
python3 -m neo_collector_adx --env .env --config config.demo.yaml --once
```

3. Rodar continuamente:

```bash
python3 -m neo_collector_adx --env .env --config config.demo.yaml
```

## Testes

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Docker

### Build

```bash
docker build -t neo-collector-adx .
```

### Execução isolada

```bash
docker run --rm \
  --env-file .env \
  -v "$(pwd)/config.demo.yaml:/app/config.demo.yaml:ro" \
  neo-collector-adx \
  --config /app/config.demo.yaml \
  --once
```

### Compose

O `docker-compose.yaml` sobe um Neo4j local e o coletor. O acesso ao ADX continua dependendo das credenciais configuradas no `.env`.

```bash
docker compose up --build
```

## Observações práticas

- `dry_run: true` permite validar resolução de templates sem escrever no Neo4j.
- para AKS com managed identity, use `ADX_AUTH_MODE=default` ou `managed_identity`
- para desenvolvimento local com login no Azure CLI, `ADX_AUTH_MODE=az_cli` costuma ser o caminho mais simples
