# NT8 Strategy Monitor – End‑to‑End Guide

This document summarizes everything needed to run, publish, and view NinjaTrader 8 (NT8) strategy status using this repo.

## What it does

- `nt8_Status.py` tails the NT8 log files and detects strategy enable/disable events.
- It maintains an in‑memory map of the latest status per `(strategy_name, instrument)`.
- It writes a local JSON snapshot for debugging and upserts the same status into a Supabase table for remote viewing.
- A static dashboard (`web_status_dashboard/`) reads from Supabase and shows green/red tiles on any device.

## Components in this repo

- `nt8_Status.py`: the daemon that watches NT8 logs and publishes status.
- `sql/001_create_strategy_status.sql`: SQL to create the `public.strategy_status` table (run once in Supabase).
- `web_status_dashboard/`: static website (HTML/JS/CSS) that reads from Supabase and displays tiles.
- `README.md`: quick start and Pages hosting notes.

## Requirements

- Windows PC running NinjaTrader 8 (NT8).
- Python 3.9+ installed on the same PC as NT8.
- Supabase project (provided in this repo; URL + anon key are public, service role key is private on your PC).

## Configuration

`nt8_Status.py` loads `config.json` from the same folder as the script file (not the current working directory). Environment variables override `config.json` values.

Recommended `config.json` (example):

```json
{
  "supabase": {
    "url": "https://dqkdljbuqtlxnkcunkmz.supabase.co",
    "service_role_key": "SUPABASE_SERVICE_ROLE_KEY_FOR_SERVER",
    "anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRxa2RsamJ1cXRseG5rY3Vua216Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTU2MDQ3MDEsImV4cCI6MjA3MTE4MDcwMX0.Ut3dak3ZNTKjTmGe4T8RE4ZNGjq8ErckNXL4kYT8deE",
    "strategy_status_table": "strategy_status"
  },
  "strategy_status_watch": {
    "log_dir": "C:/Users/<YOU>/Documents/NinjaTrader 8/log",
    "poll_interval_sec": 1.0,
    "cooldown_min": 1,
    "match_strategies": [],
    "status_json_path": "nt8_strategy_status.json",
    "email_on_change": false,
    "patterns": {}
  }
}
```

Environment variables (take precedence over config):

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (server‑only; keep private on NT8 PC)
- `SUPABASE_ANON_KEY` (browser/public)

Optional debugging:

- `SUPABASE_DEBUG=1` to print REST upsert payloads.

## What is captured

The monitor extracts (from NT8 log lines):

- `strategy_name`: name of the strategy. If NT8 logs include an instance suffix like `'Name/12345'` we normalize to `'Name'`.
- `instrument`: e.g., `MNQ DEC25` (may be blank if not present in the log line).
- `enabled`: boolean (True when enabling, False when disabling).
- `connection`: connection label if present; otherwise empty.

The monitor treats missing values as empty strings in the JSON; when publishing to Supabase, empty instrument/connection are normalized to `"EMPTY"` so rows upsert consistently across events.

## How it detects status changes

`nt8_Status.py` uses configurable regex patterns to match lines like:

- `Enabling NinjaScript strategy 'Foo/12345'`
- `Disabling NinjaScript strategy 'Foo/12345'`

Additional patterns attempt to capture `instrument` and `connection` where NT8 includes them. You can extend/override patterns via `strategy_status_watch.patterns` in `config.json`.

On launch, the script builds an initial snapshot from the tail of the current log (last ~2MB), writes a local JSON, and then continues live tailing.

## Local JSON snapshot

- Written atomically as `nt8_strategy_status.json` next to `nt8_Status.py` by default (override via `status_json_path`).
- Structure example:

```json
{
  "updated_at": "2025-11-22T13:45:12",
  "strategies": [
    { "name": "ORBSHORT20250817_EMAIL", "instrument": "MGC DEC25", "enabled": true, "connection": "My Funded 1", "account": "" },
    { "name": "nt8stratdynamicliveNY", "instrument": "MNQ DEC25", "enabled": true, "connection": "Simtopstepx", "account": "" }
  ]
}
```

## Supabase publishing

- Table: `public.strategy_status`
- Columns:
  - `strategy_name text not null`
  - `instrument text not null`
  - `enabled boolean not null`
  - `connection text not null`
  - `updated_at timestamptz not null default now()`
- Uniqueness: one row per `(strategy_name, instrument)` via a unique index.
- Upsert: REST `POST /rest/v1/strategy_status?on_conflict=strategy_name,instrument` with `Prefer: resolution=merge-duplicates`.
- Timestamp: UTC ISO 8601 (timezone‑aware) in `updated_at`.
- Normalization: empty instrument/connection → `"EMPTY"` for consistent upserts.

Run once in Supabase SQL editor:

```sql
-- Create table for NT8 strategy status in the public schema
create table if not exists public.strategy_status (
  id            bigserial primary key,
  strategy_name text        not null,
  instrument    text        not null,
  enabled       boolean     not null,
  connection    text        not null,
  updated_at    timestamptz not null default now()
);

-- Ensure one row per (strategy_name, instrument)
create unique index if not exists strategy_status_unique_idx
  on public.strategy_status (strategy_name, instrument);
```

## Running the monitor

1) Make sure NT8 is running on the same PC.

2) Set environment variables (PowerShell):

```powershell
$env:SUPABASE_URL="https://dqkdljbuqtlxnkcunkmz.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY="<your_service_role_key>"
```

3) Start:

```powershell
python .\nt8_Status.py
```

You’ll see startup logs including the JSON output path. On any strategy change, the script updates the JSON and upserts to Supabase.

## Mobile dashboard

- Files live in `web_status_dashboard/`.
- No build tools; open `index.html` directly or host it (e.g., GitHub Pages).
- It uses Supabase JS v2 and the anon/public key in `status.js` to read from `public.strategy_status`.
- Polls every 5 seconds and renders green (enabled) / red (disabled) tiles.

### GitHub Pages (recommended hosting)

1) Commit the repo to GitHub under `agentAD25/nt8-status-tools`.
2) In repo Settings → Pages:
   - Source: Deploy from a branch
   - Branch: `main`
   - Folder: `/`
3) Visit: `https://agentAD25.github.io/nt8-status-tools/web_status_dashboard/`

## Customization

- `match_strategies`: list of substrings; when set, only lines containing any of them are processed.
- `poll_interval_sec`: how often to check the file tail when idle (default 1s).
- `patterns`: provide additional/override regexes if your NT8 logs differ.
- `email_on_change`: if true, sends an email summary on state changes (configure `email` in `config.json`).

## Troubleshooting

- “Supabase URL/key not configured”:
  - Ensure `config.json` is next to the script you run, or environment variables are set.
  - Remember: env vars override `config.json`.

- No JSON written:
  - On launch, an initial JSON is written even with zero strategies (empty set). If you don’t see it, confirm the console path and write permissions for the script’s directory.

- No Supabase rows:
  - Set `SUPABASE_DEBUG=1` before running to print the REST payload/endpoint.
  - Verify the SQL table and unique index exist (run the provided SQL once).
  - Check that the service role key is valid and has permissions (RLS disabled is fine; otherwise configure policies).

- Instrument/connection are empty:
  - Your NT8 lines may not include those fields; they’ll remain blank (and become `"EMPTY"` in Supabase for upsert consistency). You can add custom regex patterns if your log format is richer.

## Security

- `service_role_key` must remain server‑side only (NT8 PC). Do not expose it in web or commit to public repos.
- The dashboard uses only the public anon key and read‑only selects.

## File locations (defaults)

- NT8 logs: `C:\Users\<YOU>\Documents\NinjaTrader 8\log\log*.txt`
- Local status JSON: `nt8_strategy_status.json` next to `nt8_Status.py` (atomic writes).

---

If you want real‑time (push) updates in the dashboard, consider adding Supabase Realtime subscriptions in `status.js`. Polling every 5 seconds is the simple baseline and works well. 


