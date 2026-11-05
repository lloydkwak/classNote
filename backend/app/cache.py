"""JSON cache for external calls in `data/cache/`, keyed by a hash of the inputs."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import get_settings


class CacheMissInDemoMode(RuntimeError):
    """DEMO_MODE forbids external calls, and this input has no cached result."""


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_path(namespace: str, key_parts: list[Any]) -> Path:
    key = json.dumps(key_parts, ensure_ascii=False, sort_keys=True)
    name = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return get_settings().data_dir / "cache" / namespace / f"{name}.json"


def cached_json(namespace: str, key_parts: list[Any], compute: Callable[[], Any]) -> Any:
    """Return the cached value for these inputs, computing and storing it on a miss."""
    path = _cache_path(namespace, key_parts)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if get_settings().demo_mode:
        raise CacheMissInDemoMode(f"DEMO_MODE=1 and no cached {namespace} result for this input.")
    value = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write then rename, so an interrupted run can't leave a truncated cache entry.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return value
