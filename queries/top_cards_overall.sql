SELECT
  c.card_name,
  COUNT(*) AS appearances
FROM deck_cards dc
JOIN cards c ON c.card_id = dc.card_id
GROUP BY 1
ORDER BY appearances DESC
LIMIT 50;
