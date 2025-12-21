# src/clashdb/card_metadata.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_PATH = Path("src/data/card_metadata.json")


def load_card_metadata(path: Path = DEFAULT_PATH) -> Dict[str, Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: Dict[str, Dict[str, Any]] = {}
    for row in data:
        cid = str(row.get("id"))
        out[cid] = row
    return out


def card_name_from_id(meta: Dict[str, Dict[str, Any]], card_id: str) -> Optional[str]:
    row = meta.get(str(card_id))
    if not row:
        return None
    name = row.get("name")
    return str(name).strip() if name else None
