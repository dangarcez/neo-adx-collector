from __future__ import annotations

from pathlib import Path


def load_dotenv_file(path: str | None, *, required: bool = False) -> None:
    if not path:
        return

    dotenv_path = Path(path)
    if not dotenv_path.exists():
        if required:
            raise FileNotFoundError(f".env file not found: {dotenv_path}")
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_quotes(value.strip())
        if key and key not in __import__("os").environ:
            __import__("os").environ[key] = value


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
