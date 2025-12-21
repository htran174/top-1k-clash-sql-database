#src/analytics/battle_filters.py
from typing import Any, Dict, List

RANKED_MODE_ID_WHITELIST = {
    72000006,  # Ladder (Trophy Road)
    72000464,  # Ranked1v1_NewArena2 (Path of Legends or ranked)
}


def is_ranked_1v1_battle(battle: Dict[str, Any]) -> bool:
    """
    Return True if this battle is a valid ranked/Trophy Road 1v1 match.

    Conditions:
      - team/opponent are lists
      - len(team) == len(opponent) == 1  (pure 1v1)
      - gameMode.id is in our ranked whitelist
    """
    team = battle.get("team", [])
    opponent = battle.get("opponent", [])

    # Must be 1v1
    if not isinstance(team, list) or not isinstance(opponent, list):
        return False
    if len(team) != 1 or len(opponent) != 1:
        return False

    game_mode = battle.get("gameMode", {}) or {}
    mode_id = game_mode.get("id")

    # Must match one of the known ranked / ladder mode IDs
    if mode_id not in RANKED_MODE_ID_WHITELIST:
        return False

    return True


def _compute_result(team_crowns: int, opp_crowns: int) -> str:
    """
    Compute battle result from crown counts.

    Returns:
        "win" | "loss" | "draw"
    """
    if team_crowns > opp_crowns:
        return "win"
    if team_crowns < opp_crowns:
        return "loss"
    return "draw"


def normalize_battle(battle: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a raw battle dict into the minimal structure.

    Output schema (per spec):
        {
          "battle_time": str,
          "result": "win" | "loss" | "draw",
          "my_cards": List[str],
          "opp_cards": List[str],
          "mode_name": str,
        }
    """
    team = battle.get("team", [{}])
    opponent = battle.get("opponent", [{}])

    my_side = team[0] if team else {}
    opp_side = opponent[0] if opponent else {}

    my_crowns = my_side.get("crowns", 0)
    opp_crowns = opp_side.get("crowns", 0)

    result = _compute_result(my_crowns, opp_crowns)

    my_cards = [
        c.get("name", "").strip()
        for c in my_side.get("cards", [])
        if isinstance(c, dict) and c.get("name")
    ]

    opp_cards = [
        c.get("name", "").strip()
        for c in opp_side.get("cards", [])
        if isinstance(c, dict) and c.get("name")
    ]

    game_mode = battle.get("gameMode", {}) or {}
    mode_name = game_mode.get("name") or (battle.get("type") or "")

    return {
        "battle_time": battle.get("battleTime"),
        "result": result,
        "my_cards": my_cards,
        "opp_cards": opp_cards,
        "mode_name": mode_name,
    }


def filter_and_normalize_ranked_1v1(
    battles_raw: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Filter raw battlelog entries down to ranked/Trophy Road 1v1 battles
    and normalize them to a clean structure.

    Args:
        battles_raw: List of raw battle dicts from the API.

    Returns:
        List of normalized battle dicts.
    """
    normalized: List[Dict[str, Any]] = []

    for battle in battles_raw:
        if not isinstance(battle, dict):
            continue

        if not is_ranked_1v1_battle(battle):
            continue

        normalized.append(normalize_battle(battle))

    return normalized
