SELECT
  d.deck_type,
  COUNT(*) AS uses,
  SUM(CASE WHEN pd.win THEN 1 ELSE 0 END) AS wins,
  ROUND(100.0 * SUM(CASE WHEN pd.win THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 2) AS win_rate
FROM player_decks pd
JOIN decks d ON d.deck_hash = pd.deck_hash
GROUP BY 1
ORDER BY uses DESC
LIMIT 30;
