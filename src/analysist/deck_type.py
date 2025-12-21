#/src/analytics/deck_type.py
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ---------- Load card metadata ----------

BASE_DIR = Path(__file__).resolve().parents[1]  # .../src
DATA_DIR = BASE_DIR / "data"
CARD_METADATA_PATH = DATA_DIR / "card_metadata.json"

with CARD_METADATA_PATH.open("r", encoding="utf-8") as f:
    _CARD_META_LIST: List[Dict[str, Any]] = json.load(f)

# Map by card name for quick lookup
_CARD_META_BY_NAME: Dict[str, Dict[str, Any]] = {c["name"]: c for c in _CARD_META_LIST}


def _get_card_meta(card_name: str) -> Dict[str, Any]:
    """Safely get metadata for a card by name."""
    return _CARD_META_BY_NAME.get(card_name, {})


# ---------- Archetype constants ----------

ARCHETYPE_SIEGE = "Siege"
ARCHETYPE_BAIT = "Bait"
ARCHETYPE_CYCLE = "Cycle"
ARCHETYPE_BRIDGE_SPAM = "Bridge Spam"
ARCHETYPE_BEATDOWN = "Beatdown"
ARCHETYPE_HYBRID = "Hybrid"

# For Siege rules
_SIEGE_XBOW = {"X-Bow"}
_SIEGE_MORTAR = {"Mortar"}


def _precompute_deck_values(cards: List[str]) -> Dict[str, Any]:
    """
    Compute:
      - avg_elixir
      - four_card_cycle_cost
      - has_xbow / has_mortar
      - bait_pieces (using is_bait_piece or explicit names)
      - has_bait_core (Goblin Barrel + at least 1 other bait piece)
      - bridge_spam_count (using is_bridge_spam_piece)
      - big_tank_count (using is_big_tank)
    """
    metas = [_get_card_meta(c) for c in cards]

    elixirs: List[float] = [
        m["elixir"] for m in metas if isinstance(m.get("elixir"), (int, float))
    ]
    if len(elixirs) == 0:
        avg_elixir = 3.0
        four_cycle = 12.0
    else:
        # avg elixir
        avg_elixir = sum(elixirs) / 8.0  # deck = 8 cards
        # four-card cycle cost
        four_cycle = sum(sorted(elixirs)[:4])

    names_set = set(cards)

    has_xbow = len(names_set & _SIEGE_XBOW) > 0
    has_mortar = len(names_set & _SIEGE_MORTAR) > 0

    # bait_pieces – primarily from metadata flag
    bait_pieces = sum(1 for m in metas if m.get("is_bait_piece"))

    bridge_spam_count = sum(1 for m in metas if m.get("is_bridge_spam_piece"))
    big_tank_count = sum(1 for m in metas if m.get("is_big_tank"))

    return {
        "avg_elixir": avg_elixir,
        "four_card_cycle_cost": four_cycle,
        "has_xbow": has_xbow,
        "has_mortar": has_mortar,
        "bait_pieces": bait_pieces,
        "bridge_spam_count": bridge_spam_count,
        "big_tank_count": big_tank_count,
    }


def classify_deck(cards: List[str]) -> str:
    """
    Classify a deck into one of archetypes.

    Priority order (first match wins):
      1) Siege
      2) Bait
      3) Cycle
      4) Bridge Spam
      5) Beatdown
      6) Hybrid (fallback)
    """
    if not cards:
        return ARCHETYPE_HYBRID

    v = _precompute_deck_values(cards)

    avg_elixir = v["avg_elixir"]
    four_cycle = v["four_card_cycle_cost"]
    has_xbow = v["has_xbow"]
    has_mortar = v["has_mortar"]
    bait_pieces = v["bait_pieces"]
    bridge_spam_count = v["bridge_spam_count"]
    big_tank_count = v["big_tank_count"]

    # =========================================
    # SIEGE RULES
    # =========================================
    # S1: X-Bow hard rule
    if has_xbow:
        return ARCHETYPE_SIEGE

    # S2: Mortar hard rule
    if has_mortar:
        return ARCHETYPE_SIEGE

    # =========================================
    # BAIT RULES (PACKAGE-BASED)
    # =========================================
    # B1: At least 3 Bait pieces
    if bait_pieces >= 3:
        return ARCHETYPE_BAIT

    # =========================================
    # CYCLE RULES (4-card cycle cost)
    # =========================================
    # CY1: If four_card_cycle_cost <= 9 -> Cycle
    if four_cycle <= 9:
        return ARCHETYPE_CYCLE

    # =========================================
    # BRIDGE SPAM RULES (key piece count)
    # =========================================
    # BS1: If bridge_spam_count >= 2 -> Bridge Spam
    if bridge_spam_count >= 2:
        return ARCHETYPE_BRIDGE_SPAM

    # =========================================
    # BEATDOWN RULES (tank + heavy avg)
    # =========================================
    # BD1: If big_tank_count >= 1 AND avg_elixir >= 3.5 -> Beatdown
    if big_tank_count >= 1 and avg_elixir >= 3.5:
        return ARCHETYPE_BEATDOWN

    # =========================================
    # HYBRID (fallback)
    # =========================================
    return ARCHETYPE_HYBRID

# -----------------------------------------
# Aggregation helpers: deck-type stats
# -----------------------------------------

def _deck_type_stats_to_list(
    stats: Dict[str, Dict[str, int]]
) -> List[Dict[str, Any]]:
    """
    Turn a mapping like:
        {
          "cycle": {"games": 10, "wins": 6, "losses": 4, "draws": 0},
          ...
        }
    into a sorted list of dicts with win_rate.
    """
    out: List[Dict[str, Any]] = []
    for deck_type, s in stats.items():
        games = s["games"]
        wins = s["wins"]
        losses = s["losses"]
        draws = s["draws"]
        win_rate = wins / games if games > 0 else 0.0

        out.append(
            {
                "type": deck_type,
                "games": games,
                "wins": wins,
                "losses": losses,
                "draws": draws,
                "win_rate": win_rate,
            }
        )

    # Sort by win_rate desc, then by games desc (more games = more reliable)
    out.sort(key=lambda x: (x["win_rate"], x["games"]), reverse=True)
    return out

def summarize_deck_types(
    battles_normalized: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Given normalized battles, aggregate performance by deck archetype.

    Each battle in `battles_normalized` is expected to have:
        - "result": "win" | "loss" | "draw"
        - "my_cards":  list of 8 card names (your deck)
        - "opp_cards": list of 8 card names (opponent deck)

    Returns:
        (my_deck_types, opp_deck_types)

    where each is a list of dicts like:
        {
          "type": "cycle",
          "games": 25,
          "wins": 14,
          "losses": 11,
          "draws": 0,
          "win_rate": 0.56,
        }
    """

    # Aggregate stats separately
    my_stats: Dict[str, Dict[str, int]] = {}
    opp_stats: Dict[str, Dict[str, int]] = {}

    def _ensure_bucket(stats: Dict[str, Dict[str, int]], key: str) -> Dict[str, int]:
        if key not in stats:
            stats[key] = {"games": 0, "wins": 0, "losses": 0, "draws": 0}
        return stats[key]

    for battle in battles_normalized:
        result = battle.get("result")
        my_cards = battle.get("my_cards") or []
        opp_cards = battle.get("opp_cards") or []

        # Expect 8 cards; if not, just skip this battle for deck-type stats
        try:
            if len(my_cards) == 8:
                my_type = classify_deck(my_cards)
            else:
                # skip weird decks instead of raising
                my_type = None
        except Exception:
            my_type = None

        try:
            if len(opp_cards) == 8:
                opp_type = classify_deck(opp_cards)
            else:
                opp_type = None
        except Exception:
            opp_type = None

        # Update "my" deck-type stats
        if my_type is not None:
            bucket = _ensure_bucket(my_stats, my_type)
            bucket["games"] += 1
            if result == "win":
                bucket["wins"] += 1
            elif result == "loss":
                bucket["losses"] += 1
            else:
                bucket["draws"] += 1

        # Update "opponent" deck-type stats (flip win/loss perspective)
        if opp_type is not None:
            bucket = _ensure_bucket(opp_stats, opp_type)
            bucket["games"] += 1
            if result == "win":
                bucket["losses"] += 1  
            elif result == "loss":
                bucket["wins"] += 1     
            else:
                bucket["draws"] += 1

    my_deck_types = _deck_type_stats_to_list(my_stats)
    opp_deck_types = _deck_type_stats_to_list(opp_stats)

    return my_deck_types, opp_deck_types

# ---------- Aggregation over battles ----------


def _init_type_bucket(deck_type: str) -> Dict[str, Any]:
    return {
        "type": deck_type,
        "games": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "win_rate": 0.0,
    }


def _finalize_stats(raw: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert {deck_type: stats} into a sorted list, computing win_rate (0–1).
    Sort by games desc.
    """
    result: List[Dict[str, Any]] = []
    for deck_type, s in raw.items():
        games = s["games"]
        wins = s["wins"]
        losses = s["losses"]
        draws = s["draws"]
        win_rate = wins / games if games > 0 else 0.0
        result.append(
            {
                "type": deck_type,
                "games": games,
                "wins": wins,
                "losses": losses,
                "draws": draws,
                "win_rate": win_rate,
            }
        )

    result.sort(key=lambda d: d["games"], reverse=True)
    return result

