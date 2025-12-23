SELECT
  p.player_tag,
  p.player_name,
  p.trophies,
  COUNT(pd.deck_hash) AS decks_seen
FROM player p
LEFT JOIN player_decks pd ON pd.player_tag = p.player_tag
GROUP BY 1,2,3
ORDER BY p.trophies DESC
LIMIT 50;
