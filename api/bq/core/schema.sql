-- bq/core/schema.sql · executado e testado em SQLite 3.49.1
-- Fonte: .docs/04-modelo-de-dados.md §3. Não há migrações: mudou o schema, apaga party.db.

PRAGMA foreign_keys = ON;

CREATE TABLE guest (
  id               INTEGER PRIMARY KEY,
  nickname         TEXT    NOT NULL,
  token            TEXT    NOT NULL UNIQUE,          -- valor do cookie bq_guest
  created_at       INTEGER NOT NULL,
  last_seen_at     INTEGER NOT NULL,
  last_accepted_at INTEGER,                          -- RF-09: só sugestão ACEITA atualiza
  CHECK (length(nickname) BETWEEN 2 AND 20)
);

CREATE TABLE track (
  id          TEXT    PRIMARY KEY,                   -- TrackId (22 base62)
  uri         TEXT    NOT NULL,                      -- TrackUri
  name        TEXT    NOT NULL,
  artists     TEXT    NOT NULL,                      -- já formatado "A, B"
  album       TEXT    NOT NULL,
  art_url     TEXT,
  duration_ms INTEGER NOT NULL CHECK (duration_ms > 0),
  explicit    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE play (
  id              INTEGER PRIMARY KEY,
  track_id        TEXT    NOT NULL REFERENCES track(id),
  suggestion_id   INTEGER,                           -- NULL se force-play do host
  guest_id        INTEGER REFERENCES guest(id),      -- NULL se force-play do host
  source          TEXT    NOT NULL CHECK (source IN ('guest','host_force')),
  started_at      INTEGER NOT NULL,
  ended_at        INTEGER,                           -- NULL = play aberto
  end_reason      TEXT    CHECK (end_reason IN
                    ('finished','skip_vote','host_skip','host_force','external','error')),
  duration_ms     INTEGER NOT NULL,                  -- snapshot: a faixa pode mudar no catálogo
  heard_ms        INTEGER,
  protected_until INTEGER NOT NULL DEFAULT 0,        -- RF-26; 0 = sem proteção
  CHECK (ended_at IS NULL OR ended_at   >= started_at),
  CHECK (ended_at IS NULL OR end_reason IS NOT NULL)
);
-- INV-1: no máximo UM play aberto. O índice é a garantia, não a convenção.
-- Indexa a EXPRESSÃO, não a coluna: em índice UNIQUE do SQLite os NULL são distintos entre si,
-- então `ON play(ended_at) WHERE ended_at IS NULL` permitiria infinitos plays abertos.
CREATE UNIQUE INDEX ux_play_open ON play((ended_at IS NULL)) WHERE ended_at IS NULL;
CREATE INDEX ix_play_track_end ON play(track_id, ended_at);

CREATE TABLE suggestion (
  id           INTEGER PRIMARY KEY,
  guest_id     INTEGER NOT NULL REFERENCES guest(id),
  track_id     TEXT    NOT NULL REFERENCES track(id),
  suggested_at INTEGER NOT NULL,
  rank         INTEGER NOT NULL,                     -- round-rank;  -1 = volta à frente (RF-26)
  state        TEXT    NOT NULL CHECK (state IN
                 ('queued','playing','played','skipped','removed')),
  play_id      INTEGER REFERENCES play(id),
  interrupts   INTEGER NOT NULL DEFAULT 0,           -- quantas vezes foi interrompida
  CHECK (state <> 'playing' OR play_id IS NOT NULL)  -- INV-6
);
CREATE INDEX        ix_sug_queue        ON suggestion(state, rank, suggested_at);
-- INV-2: no máximo UMA sugestão tocando.
CREATE UNIQUE INDEX ux_sug_one_playing  ON suggestion(state)    WHERE state = 'playing';
-- RF-11 / INV-3: uma faixa não pode estar duas vezes na fila (nem na fila e tocando).
CREATE UNIQUE INDEX ux_sug_active_track ON suggestion(track_id) WHERE state IN ('queued','playing');

CREATE TABLE skip_vote (
  play_id  INTEGER NOT NULL REFERENCES play(id),
  guest_id INTEGER NOT NULL REFERENCES guest(id),
  voted_at INTEGER NOT NULL,
  PRIMARY KEY (play_id, guest_id)                    -- um voto por pessoa por execução
) WITHOUT ROWID;

CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);
