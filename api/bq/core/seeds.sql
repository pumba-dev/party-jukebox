-- Limiares de jogo. Ficam no banco, e não no .env, porque RF-24 exige ajuste ao vivo
-- pelo /host sem restart. Ver .docs/04-modelo-de-dados.md §3.2.
INSERT OR IGNORE INTO setting(key, value) VALUES
  ('skip_votes_needed',   '5'),        -- RF-20
  ('suggest_cooldown_ms', '120000'),   -- RF-09 · 2 min
  ('max_duration_ms',     '420000'),   -- RF-13 · 7 min
  ('repeat_window_ms',    '5400000'),  -- RF-12 · 90 min
  ('protect_ms',          '90000'),    -- RF-26 · 90 s
  ('skip_cooldown_ms',    '45000'),    -- RF-23
  ('min_remaining_ms',    '15000'),    -- RF-23
  ('min_heard_ms',        '20000'),    -- RF-23 · literal; o teto de 25% saiu (ADR-004 §Revisão)
  ('paused',              '0');
