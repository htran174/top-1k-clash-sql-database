# src/clashdb/hash_utils.py
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Sequence, Tuple

CardKey = Tuple[str, str]  # (card_id, variant) where variant in {"base","evo","hero"}


def canonical_deck_signature(cards: Sequence[CardKey]) -> str:
    """
    Stable deck signature for hashing.
    Canonical order = sort by (card_id, variant)
    Example: "26000015:base|26000063:evo|..."
    """
    normalized: List[CardKey] = []
    for cid, variant in cards:
        normalized.append((str(cid), str(variant)))

    normalized.sort(key=lambda x: (x[0], x[1]))
    return "|".join([f"{cid}:{variant}" for cid, variant in normalized])


def deck_hash_from_signature(sig: str) -> str:
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()


def match_hash(battle: Dict[str, Any]) -> str:
    """
    Dedup hash stable across both players' battlelogs.
    Uses only fields that should match from either side.
    """
    battle_time = battle.get("battleTime") or ""

    game_mode = battle.get("gameMode") or {}
    mode_id = game_mode.get("id")
    mode_name = game_mode.get("name")
    mode_key = str(mode_id or mode_name or battle.get("type") or "")

    team = battle.get("team") or []
    opp = battle.get("opponent") or []

    def side_payload(side) -> List[Dict[str, Any]]:
        out = []
        if not isinstance(side, list):
            return out
        for p in side:
            if not isinstance(p, dict):
                continue
            tag = (p.get("tag") or "").upper()
            crowns = int(p.get("crowns") or 0)
            out.append({"tag": tag, "crowns": crowns})
        out.sort(key=lambda x: x["tag"])
        return out

    payload = {
        "battleTime": battle_time,
        "mode": mode_key,
        "team": side_payload(team),
        "opponent": side_payload(opp),
    }

    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()
