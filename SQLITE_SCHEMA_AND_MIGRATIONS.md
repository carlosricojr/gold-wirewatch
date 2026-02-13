# SQLite Schema + Migration Notes

## Current schema
`seen_items`:
- `item_key TEXT PRIMARY KEY`
- `source TEXT NOT NULL`
- `title TEXT NOT NULL`
- `url TEXT NOT NULL`
- `published_at TEXT NULL`
- `fetched_at TEXT NOT NULL`
- `relevance REAL NOT NULL`
- `severity REAL NOT NULL`
- `reasons TEXT NOT NULL`

`events`:
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `kind TEXT NOT NULL`
- `payload TEXT NOT NULL`
- `created_at TEXT NOT NULL`

## Migration approach
- Backward-compatible only by default.
- Additive changes: `ALTER TABLE ... ADD COLUMN`.
- Breaking changes: create new table, backfill in transaction, swap table names.
- Always backup DB before manual migration.

## Versioning note
Store migration versions in a future `schema_migrations` table if/when schema churn grows.
