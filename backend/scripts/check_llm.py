"""Make one minimal LLM call to confirm the configured provider and key work."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import get_provider  # noqa: E402


def main() -> int:
    provider = get_provider()
    reply = provider.complete_json('Return exactly this JSON: {"ok": true}')
    ok = json.loads(reply).get("ok") is True
    print(f"provider={provider.name} model={provider.model} ok={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
