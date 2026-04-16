# neo_collector_adx

Coletor Python configurável por YAML que executa queries no Azure Data Explorer e projeta cada linha retornada em nodes e relacionamentos no Neo4j.

## O que o projeto entrega

- execução local e em container
- configuração operacional via `.env`
- modelagem declarativa via YAML
- políticas `create`, `merge` e `merge_at_change`
- criação automática de `node_uid`, `rel_uid`, `origin`, `created_at` e `updated_at`
- documentação em `docs/`
- exemplos de configuração em `config.demo.yaml` e `configs/`

## Estrutura

```text
src/neo_collector_adx/   codigo da aplicacao
docs/                    documentacao operacional e tecnica
tests/                   testes unitarios do nucleo de regras
config.demo.yaml         exemplo principal de configuracao
configs/                 exemplos adicionais
Dockerfile               build da imagem
docker-compose.yaml      exemplo de execucao containerizada
```

## Requisitos

- Python 3.11+
- acesso ao Azure Data Explorer
- acesso ao Neo4j

## Instalação local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
cp .env.example .env
```

## Validar configuração

```bash
python3 -m neo_collector_adx --env .env --config config.demo.yaml --validate-config
```

## Executar uma vez

```bash
python3 -m neo_collector_adx --env .env --config config.demo.yaml --once
```

## Executar em loop

```bash
python3 -m neo_collector_adx --env .env --config config.demo.yaml
```

## Docker

Build:

```bash
docker build -t neo-collector-adx .
```

Execução:

```bash
docker run --rm --env-file .env -v "$(pwd)/config.demo.yaml:/app/config.demo.yaml:ro" neo-collector-adx --config /app/config.demo.yaml --once
```

Compose:

```bash
docker compose up --build
```

## Testes

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Documentação

- [Arquitetura](docs/architecture.md)
- [Configuração](docs/configuration.md)
- [Desenvolvimento local](docs/local-development.md)
