-- D1 schema for the events Worker. Apply with: npx wrangler d1 execute openshelf --file=worker/schema.sql
CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  day     TEXT NOT NULL,      -- UTC date, also the salt period of the visitor hash
  ts      INTEGER NOT NULL,   -- client time in ms, capped at server time
  visitor TEXT NOT NULL,      -- sha256(salt|day|ip|ua), first 16 hex; IP and user agent are not stored
  session TEXT,               -- random per page load, not persisted in the browser
  type    TEXT NOT NULL,      -- view, open, expand, vote, view_switch, range, layout, filter, topic, search, more, lang, signin, submit
  item    TEXT,               -- post id when the event concerns one post
  rank    INTEGER,            -- position in a ranked list, when there is one
  view    TEXT,               -- latest, trending or threads
  lang    TEXT,
  path    TEXT,
  ref     TEXT,               -- referring host only
  country TEXT,               -- from Cloudflare, two letters
  data    TEXT                -- small JSON object with the event's other fields
);
CREATE INDEX IF NOT EXISTS events_day_type ON events (day, type);
CREATE INDEX IF NOT EXISTS events_day_item ON events (day, item);

-- Anonymous votes. voter = sha256(salt|random id kept in the voter's browser); net = sha256(salt|day|ip), used only for
-- the daily abuse limits and blanked by the cron job after a day. IP addresses are never stored.
CREATE TABLE IF NOT EXISTS votes (
  item  TEXT NOT NULL,        -- post id
  voter TEXT NOT NULL,
  dir   INTEGER NOT NULL,     -- 1 up, -1 down
  net   TEXT NOT NULL,
  day   TEXT NOT NULL,        -- UTC date of the last change
  ts    INTEGER NOT NULL,
  PRIMARY KEY (item, voter)
);
CREATE INDEX IF NOT EXISTS votes_net_day ON votes (net, day);
