-- ============================================================
-- schema.sql — Clash Royale Top1K Meta Snapshot Warehouse (Postgres)
-- SNAPSHOT MODE: TRUNCATE + RELOAD each refresh
-- ============================================================

-- ---------- OPTIONAL CLEANUP ----------
-- Drop in dependency order if you want a clean reset of schema.
-- DROP TABLE IF EXISTS meta_type_cards;
-- DROP TABLE IF EXISTS player_type_cards;
-- DROP TABLE IF EXISTS meta_type_deck_ids;
-- DROP TABLE IF EXISTS meta_deck_types;
-- DROP TABLE IF EXISTS deck_type_overrides;
-- DROP TABLE IF EXISTS deck_cards;
-- DROP TABLE IF EXISTS player_decks;
-- DROP TABLE IF EXISTS decks;
-- DROP TABLE IF EXISTS cards;
-- DROP TABLE IF EXISTS deck_types;
-- DROP TABLE IF EXISTS player;

-- ============================================================
-- 0) Dimensions
-- ============================================================

-- Deck types (dimension). Keeps DECKS clean and avoids FK to a rollup table.
CREATE TABLE IF NOT EXISTS deck_types (
  deck_type  TEXT PRIMARY KEY
);

-- Players (dimension)
CREATE TABLE IF NOT EXISTS player (
  player_tag    TEXT PRIMARY KEY,
  player_name   TEXT NOT NULL,
  trophies      INTEGER,
  rank_global   INTEGER
);

-- Cards (dimension)
CREATE TABLE IF NOT EXISTS cards (
  card_id     INTEGER PRIMARY KEY,
  card_name   TEXT NOT NULL
);

-- Decks (dimension)
-- deck_hash is derived from canonical 8-card signature: sort by (card_id, card_variant), then hash.
CREATE TABLE IF NOT EXISTS decks (
  deck_hash   TEXT PRIMARY KEY,
  deck_type   TEXT NOT NULL REFERENCES deck_types(deck_type)
);

-- Optional override mechanism (dimension-like)
-- If a row exists here, ETL must use this deck_type instead of classifier output.
CREATE TABLE IF NOT EXISTS deck_type_overrides (
  deck_hash   TEXT PRIMARY KEY REFERENCES decks(deck_hash) ON DELETE CASCADE,
  deck_type   TEXT NOT NULL REFERENCES deck_types(deck_type)
);

-- ============================================================
-- 1) Bridge Tables / Base Fact
-- ============================================================

-- DECK_CARDS: defines the 8-card composition of a deck
-- Card identity is (card_id, card_variant). card_variant is NOT a PK by itself.
CREATE TABLE IF NOT EXISTS deck_cards (
  deck_hash     TEXT NOT NULL REFERENCES decks(deck_hash) ON DELETE CASCADE,
  card_id       INTEGER NOT NULL REFERENCES cards(card_id),
  card_variant  TEXT NOT NULL,
  slot          SMALLINT, -- optional; if known, 1-8

  PRIMARY KEY (deck_hash, card_id, card_variant),

  CONSTRAINT ck_deck_cards_variant
    CHECK (card_variant IN ('normal', 'evo', 'hero')),

  CONSTRAINT ck_deck_cards_slot
    CHECK (slot IS NULL OR (slot >= 1 AND slot <= 8))
);

-- PLAYER_DECKS: base fact table at grain (player_tag, deck_hash)
-- uses = number of matches where player used that deck
-- wins = number of those matches that were wins
CREATE TABLE IF NOT EXISTS player_decks (
  player_tag  TEXT NOT NULL REFERENCES player(player_tag) ON DELETE CASCADE,
  deck_hash   TEXT NOT NULL REFERENCES decks(deck_hash) ON DELETE CASCADE,
  uses        INTEGER NOT NULL DEFAULT 0,
  wins        INTEGER NOT NULL DEFAULT 0,

  PRIMARY KEY (player_tag, deck_hash),

  CONSTRAINT ck_player_decks_nonneg
    CHECK (uses >= 0 AND wins >= 0 AND wins <= uses)
);

-- ============================================================
-- 2) Rollup Tables (stored aggregates; recomputed each refresh)
-- ============================================================

-- META_DECK_TYPES: global usage + wins for each archetype
CREATE TABLE IF NOT EXISTS meta_deck_types (
  deck_type  TEXT PRIMARY KEY REFERENCES deck_types(deck_type),
  uses       INTEGER NOT NULL DEFAULT 0,
  wins       INTEGER NOT NULL DEFAULT 0,

  CONSTRAINT ck_meta_deck_types_nonneg
    CHECK (uses >= 0 AND wins >= 0 AND wins <= uses)
);

-- META_TYPE_DECK_IDS: global usage + wins for exact decks within each archetype
CREATE TABLE IF NOT EXISTS meta_type_deck_ids (
  deck_type  TEXT NOT NULL REFERENCES deck_types(deck_type),
  deck_hash  TEXT NOT NULL REFERENCES decks(deck_hash) ON DELETE CASCADE,
  uses       INTEGER NOT NULL DEFAULT 0,
  wins       INTEGER NOT NULL DEFAULT 0,

  PRIMARY KEY (deck_type, deck_hash),

  CONSTRAINT ck_meta_type_deck_ids_nonneg
    CHECK (uses >= 0 AND wins >= 0 AND wins <= uses)
);

-- META_TYPE_CARDS: global card usage + wins within each deck type
CREATE TABLE IF NOT EXISTS meta_type_cards (
  deck_type     TEXT NOT NULL REFERENCES deck_types(deck_type),
  card_id       INTEGER NOT NULL REFERENCES cards(card_id),
  card_variant  TEXT NOT NULL,
  uses          INTEGER NOT NULL DEFAULT 0,
  wins          INTEGER NOT NULL DEFAULT 0,

  PRIMARY KEY (deck_type, card_id, card_variant),

  CONSTRAINT ck_meta_type_cards_variant
    CHECK (card_variant IN ('normal', 'evo', 'hero')),

  CONSTRAINT ck_meta_type_cards_nonneg
    CHECK (uses >= 0 AND wins >= 0 AND wins <= uses)
);

-- PLAYER_TYPE_CARDS: per-player card usage + wins within deck_type
CREATE TABLE IF NOT EXISTS player_type_cards (
  player_tag    TEXT NOT NULL REFERENCES player(player_tag) ON DELETE CASCADE,
  deck_type     TEXT NOT NULL REFERENCES deck_types(deck_type),
  card_id       INTEGER NOT NULL REFERENCES cards(card_id),
  card_variant  TEXT NOT NULL,
  uses          INTEGER NOT NULL DEFAULT 0,
  wins          INTEGER NOT NULL DEFAULT 0,

  PRIMARY KEY (player_tag, deck_type, card_id, card_variant),

  CONSTRAINT ck_player_type_cards_variant
    CHECK (card_variant IN ('normal', 'evo', 'hero')),

  CONSTRAINT ck_player_type_cards_nonneg
    CHECK (uses >= 0 AND wins >= 0 AND wins <= uses)
);

-- ============================================================
-- 3) Helpful Indexes (MVP)
-- ============================================================

-- Base fact query paths
CREATE INDEX IF NOT EXISTS idx_player_decks_deck_hash
  ON player_decks(deck_hash);

CREATE INDEX IF NOT EXISTS idx_decks_deck_type
  ON decks(deck_type);

CREATE INDEX IF NOT EXISTS idx_deck_cards_card
  ON deck_cards(card_id, card_variant);

-- Rollup query paths
CREATE INDEX IF NOT EXISTS idx_meta_type_deck_ids_deck_hash
  ON meta_type_deck_ids(deck_hash);

CREATE INDEX IF NOT EXISTS idx_meta_type_cards_card
  ON meta_type_cards(card_id, card_variant);

CREATE INDEX IF NOT EXISTS idx_player_type_cards_card
  ON player_type_cards(card_id, card_variant);

-- ============================================================
-- 4) Snapshot Refresh Helper (optional)
-- ============================================================
-- TRUNCATE in child->parent order to avoid FK issues.
-- TRUNCATE TABLE
--   player_type_cards,
--   meta_type_cards,
--   meta_type_deck_ids,
--   meta_deck_types,
--   player_decks,
--   deck_cards,
--   deck_type_overrides,
--   dec
