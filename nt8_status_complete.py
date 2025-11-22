#!/usr/bin/env python3

# nt8_status_complete.py
# Python 3.9+. Monitors NinjaTrader 8 strategy status via NT8 log files and
# writes a machine-readable JSON snapshot for external tools.
#
# This file is based on nt8_Status.py, enhanced per latest requirements.
#
# Conservative, multi-shape patterns for enabled/disabled and field extraction.
# These are heuristics based on typical NT8 log phrasing; they can be extended
# or overridden via config.json.
#
# Each enabled/disabled pattern should provide at least 'name' and 'enabled'.
# 'instrument', 'connection', and 'account' are optional but preferred.
# Patterns cover both futures (e.g. MNQ DEC25) and stock symbols (e.g. AAPL).
DEFAULT_PATTERNS = {
    "enabled": [
        # Enabling NinjaScript strategy 'Foo/12345'
        r"\benabling\s+ninjascript\s+strategy\s+'(?P<name>[^']+)'",
        # Strategy 'Foo' on MGC DEC25 enabled on connection My Funded 1
        r"strategy\s+'(?P<name>[^']+)'.*?\bon\b\s+(?P<instrument>[A-Z0-9]{1,6}(?:\s+[A-Z]{3}\d{2})?)\b.*?\benabled\b.*?(?:connection|account)\s+(?P<connection>[\w\s\-\.\#]+)",
        # Enabled strategy 'Foo' for MNQ DEC25 via Sim101
        r"\benabled\b.*?strategy\s+'(?P<name>[^']+)'.*?\bfor\b\s+(?P<instrument>[A-Z0-9]{1,6}(?:\s+[A-Z]{3}\d{2})?)\b.*?(?:via|on|connection)\s+(?P<connection>[\w\s\-\.\#]+)",
        # Strategy Foo enabled (fallback name not quoted), grab instrument if present
        r"strategy\s+(?P<name>[A-Za-z0-9_\-\.]+).*?\benabled\b(?:.*?(?P<instrument>[A-Z0-9]{1,6}(?:\s+[A-Z]{3}\d{2})?))?(?:.*?(?:connection|via)\s+(?P<connection>[\w\s\-\.\#]+))?",
    ],
    "disabled": [
        # Disabling NinjaScript strategy 'Foo/12345'
        r"\bdisabling\s+ninjascript\s+strategy\s+'(?P<name>[^']+)'",
        # Strategy 'Foo' on MGC DEC25 disabled on connection My Funded 1
        r"strategy\s+'(?P<name>[^']+)'.*?\bon\b\s+(?P<instrument>[A-Z0-9]{1,6}(?:\s+[A-Z]{3}\d{2})?)\b.*?\bdisabled\b.*?(?:connection|account)\s+(?P<connection>[\w\s\-\.\#]+)",
        # Disabled strategy 'Foo' for MNQ DEC25 via Sim101
        r"\bdisabled\b.*?strategy\s+'(?P<name>[^']+)'.*?\bfor\b\s+(?P<instrument>[A-Z0-9]{1,6}(?:\s+[A-Z]{3}\d{2})?)\b.*?(?:via|on|connection)\s+(?P<connection>[\w\s\-\.\#]+)",
        # Disabled strategy 'Foo'
        r"\bdisabled\b.*?strategy\s+'(?P<name>[^']+)'(?:.*?(?P<instrument>[A-Z0-9]{1,6}(?:\s+[A-Z]{3}\d{2})?))?(?:.*?(?:connection|via|on)\s+(?P<connection>[\w\s\-\.\#]+))?",
        # Strategy Foo disabled (fallback)
        r"strategy\s+(?P<name>[A-Za-z0-9_\-\.]+).*?\bdisabled\b(?:.*?(?P<instrument>[A-Z0-9]{1,6}(?:\s+[A-Z]{3}\d{2})?))?(?:.*?(?:connection|via)\s+(?P<connection>[\w\s\-\.\#]+))?",
    ],
    # Additional field extractors, used as fallbacks when a primary pattern
    # provides enabled state but omits one of the fields.
    "extractors": {
        "name": [
            r"strategy\s+'(?P<name>[^']+)'",
            r"strategy\s+(?P<name>[A-Za-z0-9_\-\.]+)",
        ],
        "instrument": [
            # on <SYMBOL MMMYY> or on <SYMBOL MM-YY> or on <SYMBOL>
            r"\bon\s+(?P<instrument>[A-Z]{1,6}\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s?\d{2})\b",
            r"\bon\s+(?P<instrument>[A-Z]{1,6}\s+\d{2}-\d{2})\b",
            r"\bfor\s+(?P<instrument>[A-Z]{1,6}\s+(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s?\d{2})\b",
            r"\bfor\s+(?P<instrument>[A-Z]{1,6}\s+\d{2}-\d{2})\b",
            # fallback: single-symbol only when preceded by 'on' or 'for'
            r"\bon\s+(?P<instrument>[A-Z]{1,6})\b",
            r"\bfor\s+(?P<instrument>[A-Z]{1,6})\b",
        ],
        "connection": [
            r"(?:connection|via)\s+(?P<connection>[\w\s\-\.\#]+)",
        ],
        "account": [
            r"(?:account)\s+(?P<account>[\w\s\-\.\#]+)",
        ],
    },
}

import os, re, json, time, socket, threading
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

APP_NAME = "NT8 Strategy Status Watcher"
HOME = Path.home()
NT8_LOG_DIR = HOME / "Documents" / "NinjaTrader 8" / "log"

# Valid instrument helpers
_MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
_RE_FUT_MMMYY = re.compile(rf"^[A-Z]{{1,6}}\s+(?:{_MONTHS})\s?\d{{2}}$")
_RE_FUT_MMYY = re.compile(r"^[A-Z]{1,6}\s+\d{2}-\d{2}$")  # e.g., ES 03-26
_RE_SYMBOL = re.compile(r"^[A-Z]{1,6}$")  # equities/forex symbol

DEFAULT_CONFIG = {
    "email": {
        "mode": "starttls",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "username": "you@example.com",
        "password": "APP_PASSWORD_OR_SMTP_PASSWORD",
        "from_addr": "you@example.com",
        "to_addrs": ["you@example.com"],
    },
    "supabase": {
        # URL and keys are read from env if present; config.json otherwise.
        # DO NOT put service role key in any client/browser code.
        "url": "https://dqkdljbuqtlxnkcunkmz.supabase.co",
        "service_role_key": "",
        "anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRxa2RsamJ1cXRseG5rY3Vua216Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTU2MDQ3MDEsImV4cCI6MjA3MTE4MDcwMX0.Ut3dak3ZNTKjTmGe4T8RE4ZNGjq8ErckNXL4kYT8deE",
        "strategy_status_table": "strategy_status",
    },
    "strategy_status_watch": {
        "log_dir": str(NT8_LOG_DIR),
        "poll_interval_sec": 1.0,
        "cooldown_min": 1,
        "match_strategies": [],
        "status_json_path": str(Path(__file__).with_name("nt8_strategy_status.json")),
        "email_on_change": False,
        "patterns": DEFAULT_PATTERNS,
    },
}

def load_config() -> dict:
    cfg_path = Path(__file__).with_name("config.json")
    if not cfg_path.exists():
        return DEFAULT_CONFIG
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    def deep_merge(d, default):
        for k, v in default.items():
            if isinstance(v, dict):
                d[k] = deep_merge(d.get(k, {}), v)
            else:
                d.setdefault(k, v)
        return d

    return deep_merge(data, DEFAULT_CONFIG)

def send_email(cfg_email: dict, subject: str, body: str):
    msg = (
        f"From: {cfg_email['from_addr']}\r\n"
        f"To: {', '.join(cfg_email['to_addrs'])}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n{body}"
    )
    mode = cfg_email.get("mode", "starttls").lower()
    host = cfg_email["smtp_host"]
    port = int(cfg_email["smtp_port"])
    user = cfg_email["username"]
    pwd = cfg_email["password"]

    if mode == "ssl":
        import smtplib, ssl
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=10) as server:
            server.login(user, pwd)
            server.sendmail(cfg_email["from_addr"], cfg_email["to_addrs"], msg.encode("utf-8"))
    else:
        import smtplib, ssl
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(user, pwd)
            server.sendmail(cfg_email["from_addr"], cfg_email["to_addrs"], msg.encode("utf-8"))

def newest_log_file(log_dir: Path):
    cands = sorted(Path(log_dir).glob("log*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if cands:
        return cands[0]
    cands = sorted(Path(log_dir).glob("log*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class SimpleTailer:
    def __init__(self, log_dir: Path, interval: float):
        self.log_dir = Path(log_dir)
        self.interval = interval
        self.current = None
        self.fh = None

    def open_latest(self):
        latest = newest_log_file(self.log_dir)
        if latest and latest != self.current:
            if self.fh:
                try:
                    self.fh.close()
                except Exception:
                    pass
            self.current = latest
            self.fh = open(latest, "r", encoding="utf-8", errors="ignore")
            self.fh.seek(0, os.SEEK_END)

    def lines(self):
        while True:
            self.open_latest()
            if not self.fh:
                time.sleep(self.interval)
                continue
            line = self.fh.readline()
            if not line:
                time.sleep(self.interval)
                continue
            yield line.rstrip("\r\n")

def requires_any(text: str, subs):
    if not subs:
        return True
    low = text.lower()
    return any(s.lower() in low for s in subs)

@dataclass
class StrategyStatus:
    name: str
    instrument: str
    enabled: bool
    connection: str
    account: str = ""

def parse_with_patterns(line: str, patterns: dict):
    """
    Try the configured enabled/disabled patterns, returning a tuple:
      (matched: bool, status_dict: dict)
    status_dict keys: name, instrument, enabled, connection, account(optional)
    """
    low = line.lower()

    for pat in patterns.get("enabled", []):
        m = re.search(pat, low, re.IGNORECASE)
        if m:
            gd = {k: (m.group(k) or "").strip() for k in ("name", "instrument", "connection") if k in m.groupdict()}
            # Normalize names like 'Foo/12345' -> 'Foo'
            if "name" in gd and "/" in gd["name"]:
                gd["name"] = gd["name"].split("/", 1)[0]
            status = {"enabled": True, **gd}
            return True, status

    for pat in patterns.get("disabled", []):
        m = re.search(pat, low, re.IGNORECASE)
        if m:
            gd = {k: (m.group(k) or "").strip() for k in ("name", "instrument", "connection") if k in m.groupdict()}
            if "name" in gd and "/" in gd["name"]:
                gd["name"] = gd["name"].split("/", 1)[0]
            status = {"enabled": False, **gd}
            return True, status

    return False, {}

def fill_missing_fields(line: str, status: dict, patterns: dict):
    """Use extractor patterns to fill any missing fields."""
    low = line.lower()
    extractors = patterns.get("extractors", {})
    for field in ("name", "instrument", "connection", "account"):
        if status.get(field):
            continue
        for pat in extractors.get(field, []):
            m = re.search(pat, low, re.IGNORECASE)
            if m and field in m.groupdict():
                status[field] = (m.group(field) or "").strip()
                break
    # Sanity trims
    for k in ("name", "instrument", "connection", "account"):
        if k in status and isinstance(status[k], str):
            status[k] = status[k].strip(" :;,-[]()")
    # Validate instrument to avoid false positives like bare years (e.g., "2025")
    if status.get("instrument"):
        if not (
            _RE_FUT_MMMYY.match(status["instrument"])
            or _RE_FUT_MMYY.match(status["instrument"])
            or _RE_SYMBOL.match(status["instrument"])
        ):
            status["instrument"] = ""
    return status

def atomic_write_json(path: Path, data: dict):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def statuses_to_json(statuses: dict) -> dict:
    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "strategies": [asdict(s) for s in sorted(statuses.values(), key=lambda s: (s.name.lower(), s.instrument.lower()))],
    }

def get_env_or_config(cfg: dict, env_name: str, path_in_cfg, default=None):
    """
    Look up value from environment first, otherwise from nested config path.
    path_in_cfg can be a list like ["supabase", "url"] or a dot string.
    """
    val = os.environ.get(env_name)
    if val:
        return val
    if isinstance(path_in_cfg, str):
        path = path_in_cfg.split(".")
    else:
        path = list(path_in_cfg)
    cur = cfg
    try:
        for k in path:
            cur = cur[k]
        return cur if cur not in (None, "") else default
    except Exception:
        return default

class SupabaseStrategyPublisher:
    """
    Minimal REST publisher using standard library only.
    Uses a Supabase API key (service role or anon) via env/config for upserts.
    """
    def __init__(self, cfg: dict):
        self.url = get_env_or_config(cfg, "SUPABASE_URL", ["supabase", "url"], "")
        self.service_key = get_env_or_config(cfg, "SUPABASE_SERVICE_ROLE_KEY", ["supabase", "service_role_key"], "")
        self.anon_key = get_env_or_config(cfg, "SUPABASE_ANON_KEY", ["supabase", "anon_key"], "")
        if not self.service_key:
            self.service_key = self.anon_key
        table = cfg.get("supabase", {}).get("strategy_status_table", "strategy_status")
        self.table = table
        self.rest_endpoint = self._build_table_endpoint(self.table)
        self.debug = os.environ.get("SUPABASE_DEBUG", "").lower() in ("1", "true", "yes")
        if not self.url:
            print("[warn] Supabase URL not configured; publishing disabled.")
        if not self.service_key:
            print("[warn] Supabase API key not configured; publishing disabled.")

    def _build_table_endpoint(self, table_name: str) -> str:
        base = self.url.rstrip("/")
        return f"{base}/rest/v1/{table_name}?on_conflict=strategy_name,instrument"

    def is_configured(self) -> bool:
        return bool(self.url and self.service_key)

    def upsert_status(self, strategy_name: str, instrument: str, enabled: bool, connection: str) -> None:
        if not self.is_configured():
            return
        # Normalize empties to a sentinel to avoid confusion between NULL/empty-string
        norm_instrument = instrument if instrument else "EMPTY"
        norm_connection = connection if connection else "EMPTY"
        payload = {
            "strategy_name": strategy_name,
            "instrument": norm_instrument,
            "enabled": bool(enabled),
            "connection": norm_connection,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        data = json.dumps(payload).encode("utf-8")
        try:
            import urllib.request
            req = urllib.request.Request(
                self.rest_endpoint,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "apikey": self.service_key,
                    "Authorization": f"Bearer {self.service_key}",
                    "Prefer": "resolution=merge-duplicates",
                },
            )
            if self.debug:
                print(f"[supabase] POST {self.rest_endpoint} payload={payload}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                # 201 Created or 204 No Content are typical
                if resp.status not in (200, 201, 204):
                    body = resp.read().decode("utf-8", errors="ignore")
                    print(f"[warn] Supabase upsert unexpected status: {resp.status} body={body}")
        except Exception as e:
            print(f"[error] Supabase upsert failed: {e}")

def _read_last_lines(path: Path, max_bytes: int = 2_000_000) -> list:
    """
    Efficiently read the last max_bytes of a text file and split into lines.
    Decoded as UTF-8 with errors ignored.
    """
    try:
        with open(path, "rb") as fb:
            fb.seek(0, os.SEEK_END)
            size = fb.tell()
            fb.seek(max(0, size - max_bytes), os.SEEK_SET)
            data = fb.read()
        return data.decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return []

def build_initial_statuses(log_dir: Path, patterns: dict, match_strategies) -> dict:
    """
    Parse the tail of the current newest log file to reconstruct the
    latest known status for each strategy. Returns a dict of StrategyStatus.
    """
    latest = newest_log_file(log_dir)
    statuses = {}
    if not latest:
        return statuses
    for raw_line in _read_last_lines(latest):
        if not raw_line:
            continue
        if match_strategies and not requires_any(raw_line, match_strategies):
            continue
        matched, status = parse_with_patterns(raw_line, patterns)
        if not matched:
            continue
        status = fill_missing_fields(raw_line, status, patterns)
        if not status.get("name"):
            continue
        name = status.get("name", "")
        instrument = status.get("instrument", "")
        connection = status.get("connection", "")
        enabled = bool(status.get("enabled"))
        account = status.get("account", "")
        key = (name, instrument or "")
        statuses[key] = StrategyStatus(
            name=name,
            instrument=instrument,
            enabled=enabled,
            connection=connection,
            account=account,
        )
    return statuses

def run_strategy_status_monitor(cfg: dict):
    watch = cfg["strategy_status_watch"]
    log_dir = Path(watch["log_dir"]).expanduser()
    interval = watch["poll_interval_sec"]
    cooldown_min = watch["cooldown_min"]
    match_strategies = watch.get("match_strategies", [])
    status_json_path = Path(watch["status_json_path"]).expanduser()
    patterns = watch.get("patterns", DEFAULT_PATTERNS)
    email_on_change = bool(watch.get("email_on_change", False))

    statuses = {}  # key: (name, instrument) -> StrategyStatus
    tailer = SimpleTailer(log_dir, interval)
    last_json_write = None
    last_email_time = None
    publisher = SupabaseStrategyPublisher(cfg)

    print(f"{APP_NAME} starting...")
    print(f"Watching logs in: {log_dir}")
    print(f"Writing status JSON to: {status_json_path}")

    # Initial snapshot: parse current log tail and publish immediately
    try:
        initial = build_initial_statuses(log_dir, patterns, match_strategies)
        statuses.update(initial)
        atomic_write_json(status_json_path, statuses_to_json(statuses))
        last_json_write = datetime.now()
        print(f"Initial status JSON written ({len(statuses)} strategies).")
    except Exception as e:
        print(f"[error] Failed to build initial snapshot: {e}")

    for raw_line in tailer.lines():
        try:
            if not raw_line:
                continue
            if match_strategies and not requires_any(raw_line, match_strategies):
                continue

            matched, status = parse_with_patterns(raw_line, patterns)
            if not matched:
                # Not a strategy status line; skip quietly.
                continue

            status = fill_missing_fields(raw_line, status, patterns)
            if not status.get("name"):
                # If we still cannot identify the strategy name, log and continue.
                print(f"[warn] Could not determine strategy name from line:\n  {raw_line}")
                continue

            name = status.get("name", "")
            instrument = status.get("instrument", "")
            connection = status.get("connection", "")
            enabled = bool(status.get("enabled"))
            account = status.get("account", "")
            # Normalize instrument post-extraction as well
            if instrument and not (_RE_FUT_MMMYY.match(instrument) or _RE_FUT_MMYY.match(instrument) or _RE_SYMBOL.match(instrument)):
                instrument = ""
            key = (name, instrument or "")
            prev = statuses.get(key)

            # Determine whether anything meaningful changed
            changed = (
                prev is None
                or prev.enabled != enabled
                or prev.connection != connection
                or prev.instrument != instrument
            )

            if changed:
                statuses[key] = StrategyStatus(
                    name=name,
                    instrument=instrument,
                    enabled=enabled,
                    connection=connection,
                    account=account,
                )
                print(f"[{now_str()}] Strategy change: name='{name}', instrument='{instrument}', enabled={enabled}, connection='{connection}'")

                # Write JSON snapshot
                try:
                    atomic_write_json(status_json_path, statuses_to_json(statuses))
                    last_json_write = datetime.now()
                    print(f"Status JSON updated ({len(statuses)} strategies).")
                except Exception as e:
                    print(f"[error] Failed to write status JSON: {e}")

                # Publish to Supabase
                try:
                    publisher.upsert_status(name, instrument, enabled, connection)
                except Exception as e:
                    print(f"[error] Failed to publish to Supabase: {e}")

                # Optional email on change (rate-limited by cooldown)
                if email_on_change:
                    can_email = (
                        last_email_time is None or datetime.now() - last_email_time > timedelta(minutes=cooldown_min)
                    )
                    if can_email:
                        try:
                            subject = f"[{APP_NAME}] Change: {name} {'ENABLED' if enabled else 'DISABLED'}"
                            body = (
                                f"{now_str()} Strategy status changed\n"
                                f"Name: {name}\n"
                                f"Instrument: {instrument}\n"
                                f"Enabled: {enabled}\n"
                                f"Connection: {connection}\n"
                                f"Log: {tailer.current}\n"
                            )
                            send_email(cfg["email"], subject, body)
                            last_email_time = datetime.now()
                        except Exception as e:
                            print(f"[error] Email on change failed: {e}")
        except Exception as e:
            # Always continue on errors; print the offending line for diagnostics.
            print(f"[error] Exception while processing line: {e}\n  line: {raw_line}")

def main():
    cfg = load_config()
    try:
        run_strategy_status_monitor(cfg)
    except KeyboardInterrupt:
        print("Exiting")
    except Exception as e:
        print(f"Fatal error: {e}")

if __name__ == "__main__":
    main()


