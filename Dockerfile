FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir .

COPY config.demo.yaml /app/config.demo.yaml
COPY docs /app/docs

ENTRYPOINT ["neo-collector-adx"]
CMD ["--config", "/app/config.demo.yaml"]
