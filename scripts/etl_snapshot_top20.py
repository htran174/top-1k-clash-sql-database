# scripts/etl_snapshot_top20.py
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --- make `src.*` imports work when running from repo root or as a script ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from src.api.players import fetch_top_players
from src.api.battles import get_player_battlelog
from src.analysist.battle_filters import is_ranked_1v1_battle
from src.analysist.deck_type import classify_deck

from src.clashdb.db import get_engine
from src.clashdb.hash_utils import canonical_deck_signature, deck_hash_from_signature, match_hash
from src.clashdb.card_metadata import load_card_metadata, card_name_from_id


# ----------------------------
# Helpers / Normalization
# ----------------------------

def _normalize_tag(tag: Any) -> str:
    t = (tag or "").strip().upper()
    if t and not t.startswith("#"):
        t = "#" + t
    return t


def card_variant_from_evolution_level(evolution_level: Any) -> str:
    """
    Simple rule:
      evolutionLevel == 1 -> evo
      evolutionLevel == 2 -> hero
      else (missing/0/other) -> normal
    """
    try:
        lvl = int(evolution_level or 0)
    except Exception:
        lvl = 0

    if lvl == 1:
        return "evo"
    if lvl == 2:
        return "hero"
    return "normal"


@dataclass(frozen=True)
class CardObs:
    card_id: int
    card_name: str
    card_variant: str
    slot: int  # 1..8


def _extract_8_cards(participant: Dict[str, Any], card_meta: Dict[str, Dict[str, Any]]) -> Optional[List[CardObs]]:
    cards = participant.get("cards") or []
    if not isinstance(cards, list) or len(cards) < 8:
        return None

    out: List[CardObs] = []
    for idx, c in enumerate(cards[:8], start=1):
        if not isinstance(c, dict):
            return None

        cid = c.get("id")
        if cid is None:
            return None
        cid_int = int(cid)

        nm = (c.get("name") or "").strip()
        if not nm:
            nm = card_name_from_id(card_meta, str(cid_int)) or ""

        variant = card_variant_from_evolution_level(c.get("evolutionLevel", 0))

        out.append(CardObs(card_id=cid_int, card_name=nm, card_variant=variant, slot=idx))

    # Must be exactly 8 unique (card_id, card_variant)
    uniq = {(x.card_id, x.card_variant) for x in out}
    if len(uniq) != 8:
        return None

    return out


def _participant_is_win_ranked_1v1(battle: Dict[str, Any], participant_tag: str) -> bool:
    """
    Winner by crowns for ranked 1v1 (your filter ensures this is a ranked 1v1 match).
    """
    participant_tag = _normalize_tag(participant_tag)

    team = battle.get("team") or []
    opp = battle.get("opponent") or []

    if not (isinstance(team, list) and isinstance(opp, list) and len(team) == 1 and len(opp) == 1):
        return False

    t0 = team[0] if isinstance(team[0], dict) else {}
    o0 = opp[0] if isinstance(opp[0], dict) else {}

    team_tag = _normalize_tag(t0.get("tag"))
    opp_tag = _normalize_tag(o0.get("tag"))

    team_crowns = int(t0.get("crowns") or 0)
    opp_crowns = int(o0.get("crowns") or 0)

    if participant_tag == team_tag:
        return team_crowns > opp_crowns
    if participant_tag == opp_tag:
        return opp_crowns > team_crowns

    return False


# ----------------------------
# DB I/O
# ----------------------------

def load_deck_type_overrides(conn) -> Dict[str, str]:
    """
    deck_type_overrides should NOT be truncated (manual config).
    """
    overrides: Dict[str, str] = {}
    rows = conn.execute(text("SELECT deck_hash, deck_type FROM deck_type_overrides")).fetchall()
    for dh, dt in rows:
        if dh and dt:
            overrides[str(dh)] = str(dt)
    return overrides


def truncate_snapshot_tables(conn) -> None:
    """
    Match your schema table names. DO NOT truncate deck_type_overrides.
    Child -> parent order.
    """
    ordered = [
        "player_type_cards",
        "meta_type_cards",
        "meta_type_deck_ids",
        "meta_deck_types",
        "player_decks",
        "deck_cards",
        "decks",
        "cards",
        "player",
        "deck_types",
    ]

    for t in ordered:
        conn.execute(text(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE;"))


# ----------------------------
# Main ETL
# ----------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    engine = get_engine()
    card_meta = load_card_metadata()

    # Load overrides first
    with engine.begin() as conn:
        deck_type_overrides = load_deck_type_overrides(conn)

    # Fetch Top N players
    raw = fetch_top_players(limit=max(args.top_n, 20))[: args.top_n]
    top_players: List[Dict[str, Any]] = []
    for i, p in enumerate(raw, start=1):
        tag = _normalize_tag(p.get("tag"))
        if not tag:
            continue
        top_players.append(
            {
                "player_tag": tag,
                "player_name": (p.get("name") or "").strip(),
                "trophies": int(p.get("trophies") or 0),
                "rank_global": int(p.get("rank") or i),
            }
        )

    top_tags = {p["player_tag"] for p in top_players}

    # Dedup + scan stats
    seen_matches = set()
    scanned_entries = 0
    deduped_matches = 0

    # Dimensions we must populate for meta (cards/decks)
    cards_dim: Dict[int, str] = {}
    deck_hash_to_type: Dict[str, str] = {}
    deck_hash_to_cards: Dict[str, List[CardObs]] = {}

    # Player facts (TopN only)
    player_decks_top = defaultdict(lambda: {"uses": 0, "wins": 0})

    # Meta facts (both sides)
    meta_deck_types = defaultdict(lambda: {"uses": 0, "wins": 0})
    meta_type_deck_ids = defaultdict(lambda: {"uses": 0, "wins": 0})
    meta_type_cards = defaultdict(lambda: {"uses": 0, "wins": 0})

    # Scan battlelogs for TopN only
    for pl in top_players:
        ptag = pl["player_tag"]
        battles = get_player_battlelog(ptag)
        if not isinstance(battles, list):
            continue

        scanned_entries += len(battles)

        for b in battles:
            if not isinstance(b, dict):
                continue
            if not is_ranked_1v1_battle(b):
                continue

            mh = match_hash(b)
            if mh in seen_matches:
                continue
            seen_matches.add(mh)
            deduped_matches += 1

            team = b.get("team") or []
            opp = b.get("opponent") or []
            if not (isinstance(team, list) and isinstance(opp, list) and len(team) == 1 and len(opp) == 1):
                continue

            # Process BOTH sides for meta
            for part in (team[0], opp[0]):
                if not isinstance(part, dict):
                    continue

                tag = _normalize_tag(part.get("tag"))
                if not tag:
                    continue

                card_obs = _extract_8_cards(part, card_meta)
                if card_obs is None:
                    continue

                # Build deck hash (canonical sorted by card_id,variant)
                card_keys = [(str(c.card_id), c.card_variant) for c in card_obs]
                sig = canonical_deck_signature(card_keys)
                dh = deck_hash_from_signature(sig)

                # Deck type (override if exists else classifier)
                names_for_classifier = [c.card_name for c in card_obs if c.card_name]
                dtype = deck_type_overrides.get(dh) or classify_deck(names_for_classifier)

                won = _participant_is_win_ranked_1v1(b, tag)

                # Store deck dims once
                if dh not in deck_hash_to_type:
                    deck_hash_to_type[dh] = dtype
                    deck_hash_to_cards[dh] = card_obs

                # Store cards dim
                for c in card_obs:
                    if c.card_name:
                        cards_dim[c.card_id] = c.card_name

                # META aggregates always (both sides)
                meta_deck_types[dtype]["uses"] += 1
                meta_deck_types[dtype]["wins"] += 1 if won else 0

                meta_type_deck_ids[(dtype, dh)]["uses"] += 1
                meta_type_deck_ids[(dtype, dh)]["wins"] += 1 if won else 0

                for c in card_obs:
                    meta_type_cards[(dtype, c.card_id, c.card_variant)]["uses"] += 1
                    meta_type_cards[(dtype, c.card_id, c.card_variant)]["wins"] += 1 if won else 0

                # PLAYER aggregates only for TopN tags
                if tag in top_tags:
                    player_decks_top[(tag, dh)]["uses"] += 1
                    player_decks_top[(tag, dh)]["wins"] += 1 if won else 0

    # Derive player_type_cards from TopN player_decks
    player_type_cards_top = defaultdict(lambda: {"uses": 0, "wins": 0})
    for (ptag, dh), rec in player_decks_top.items():
        dtype = deck_hash_to_type.get(dh, "Hybrid")
        uses = int(rec["uses"])
        wins = int(rec["wins"])
        for c in deck_hash_to_cards.get(dh, []):
            player_type_cards_top[(ptag, dtype, c.card_id, c.card_variant)]["uses"] += uses
            player_type_cards_top[(ptag, dtype, c.card_id, c.card_variant)]["wins"] += wins

    # Summary
    print("\n[ETL] SUMMARY (pre-DB)")
    print(f"  players fetched:            {len(top_players)}")
    print(f"  battle entries scanned:     {scanned_entries}")
    print(f"  deduped matches counted:    {deduped_matches}")
    print(f"  unique decks:               {len(deck_hash_to_type)}")
    print(f"  player_decks rows (TopN):   {len(player_decks_top)}")

    if args.dry_run:
        print("\n[ETL] Dry-run mode: no DB writes.")
        return

    # ----------------------------
    # DB load (TRUNCATE + RELOAD)
    # ----------------------------
    with engine.begin() as conn:
        truncate_snapshot_tables(conn)

        # deck_types (labels required for FK)
        deck_type_labels = sorted(set(deck_hash_to_type.values()))
        if deck_type_labels:
            conn.execute(
                text("INSERT INTO deck_types (deck_type) VALUES (:deck_type)"),
                [{"deck_type": dt} for dt in deck_type_labels],
            )

        # cards (upsert)
        if cards_dim:
            conn.execute(
                text("""
                    INSERT INTO cards (card_id, card_name)
                    VALUES (:card_id, :card_name)
                    ON CONFLICT (card_id) DO UPDATE SET card_name = EXCLUDED.card_name
                """),
                [{"card_id": cid, "card_name": nm} for cid, nm in cards_dim.items()],
            )

        # player (TopN only) (upsert)
        if top_players:
            conn.execute(
                text("""
                    INSERT INTO player (player_tag, player_name, trophies, rank_global)
                    VALUES (:player_tag, :player_name, :trophies, :rank_global)
                    ON CONFLICT (player_tag) DO UPDATE SET
                        player_name = EXCLUDED.player_name,
                        trophies = EXCLUDED.trophies,
                        rank_global = EXCLUDED.rank_global
                """),
                top_players,
            )

        # decks (upsert)
        if deck_hash_to_type:
            conn.execute(
                text("""
                    INSERT INTO decks (deck_hash, deck_type)
                    VALUES (:deck_hash, :deck_type)
                    ON CONFLICT (deck_hash) DO UPDATE SET deck_type = EXCLUDED.deck_type
                """),
                [{"deck_hash": dh, "deck_type": dt} for dh, dt in deck_hash_to_type.items()],
            )

        # deck_cards (upsert)
        dc_rows = []
        for dh, obs in deck_hash_to_cards.items():
            for c in obs:
                dc_rows.append(
                    {
                        "deck_hash": dh,
                        "card_id": c.card_id,
                        "card_variant": c.card_variant,
                        "slot": c.slot,
                    }
                )
        if dc_rows:
            conn.execute(
                text("""
                    INSERT INTO deck_cards (deck_hash, card_id, card_variant, slot)
                    VALUES (:deck_hash, :card_id, :card_variant, :slot)
                    ON CONFLICT (deck_hash, card_id, card_variant)
                    DO UPDATE SET slot = EXCLUDED.slot
                """),
                dc_rows,
            )

        # player_decks (TopN only) (upsert)
        pd_rows = []
        for (ptag, dh), rec in player_decks_top.items():
            pd_rows.append(
                {
                    "player_tag": ptag,
                    "deck_hash": dh,
                    "uses": int(rec["uses"]),
                    "wins": int(rec["wins"]),
                }
            )
        if pd_rows:
            conn.execute(
                text("""
                    INSERT INTO player_decks (player_tag, deck_hash, uses, wins)
                    VALUES (:player_tag, :deck_hash, :uses, :wins)
                    ON CONFLICT (player_tag, deck_hash)
                    DO UPDATE SET uses = EXCLUDED.uses, wins = EXCLUDED.wins
                """),
                pd_rows,
            )

        # meta_deck_types
        mdt_rows = [{"deck_type": dt, "uses": v["uses"], "wins": v["wins"]} for dt, v in meta_deck_types.items()]
        if mdt_rows:
            conn.execute(
                text("""
                    INSERT INTO meta_deck_types (deck_type, uses, wins)
                    VALUES (:deck_type, :uses, :wins)
                """),
                mdt_rows,
            )

        # meta_type_deck_ids
        mtdi_rows = [
            {"deck_type": dt, "deck_hash": dh, "uses": v["uses"], "wins": v["wins"]}
            for (dt, dh), v in meta_type_deck_ids.items()
        ]
        if mtdi_rows:
            conn.execute(
                text("""
                    INSERT INTO meta_type_deck_ids (deck_type, deck_hash, uses, wins)
                    VALUES (:deck_type, :deck_hash, :uses, :wins)
                """),
                mtdi_rows,
            )

        # meta_type_cards
        mtc_rows = [
            {"deck_type": dt, "card_id": cid, "card_variant": var, "uses": v["uses"], "wins": v["wins"]}
            for (dt, cid, var), v in meta_type_cards.items()
        ]
        if mtc_rows:
            conn.execute(
                text("""
                    INSERT INTO meta_type_cards (deck_type, card_id, card_variant, uses, wins)
                    VALUES (:deck_type, :card_id, :card_variant, :uses, :wins)
                """),
                mtc_rows,
            )

        # player_type_cards (TopN only)
        ptc_rows = [
            {
                "player_tag": ptag,
                "deck_type": dt,
                "card_id": cid,
                "card_variant": var,
                "uses": v["uses"],
                "wins": v["wins"],
            }
            for (ptag, dt, cid, var), v in player_type_cards_top.items()
        ]
        if ptc_rows:
            conn.execute(
                text("""
                    INSERT INTO player_type_cards (player_tag, deck_type, card_id, card_variant, uses, wins)
                    VALUES (:player_tag, :deck_type, :card_id, :card_variant, :uses, :wins)
                """),
                ptc_rows,
            )

    print("\n[ETL] Load complete.")
    print("[ETL] Quick checks:")
    print("  SELECT COUNT(*) FROM player;")
    print("  SELECT COUNT(*) FROM player_decks;")
    print("  SELECT deck_hash, COUNT(*) FROM deck_cards GROUP BY deck_hash HAVING COUNT(*) <> 8;")
    print("  SELECT deck_type, uses FROM meta_deck_types ORDER BY uses DESC LIMIT 10;")
    print("  SELECT deck_hash, SUM(uses) uses, SUM(wins) wins, (SUM(wins)::float/NULLIF(SUM(uses),0)) winrate")
    print("    FROM player_decks GROUP BY deck_hash HAVING SUM(uses) >= 5 ORDER BY winrate DESC LIMIT 10;")


if __name__ == "__main__":
    main()
