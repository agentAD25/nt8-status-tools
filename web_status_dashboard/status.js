(function () {
  "use strict";

  const cfg = (window && window.SUPABASE_STATUS_CONFIG) || {};
  const REQUIRED = ["url", "anonKey"];
  const missing = REQUIRED.filter((k) => !cfg[k] || String(cfg[k]).trim() === "");
  const table = cfg.table || "strategy_status";
  const pollMs = Number.isFinite(cfg.pollMs) ? cfg.pollMs : 5000;

  const statusMsgEl = document.getElementById("status-msg");
  const lastUpdatedEl = document.getElementById("last-updated");
  const gridEl = document.getElementById("grid");

  function setStatus(message, isError = false) {
    if (!statusMsgEl) return;
    statusMsgEl.textContent = message || "";
    statusMsgEl.className = isError ? "error" : "";
  }

  function setBusy(isBusy) {
    if (gridEl) {
      gridEl.setAttribute("aria-busy", isBusy ? "true" : "false");
    }
  }

  function fmtTime(iso) {
    try {
      return new Date(iso).toLocaleString();
    } catch (e) {
      return iso || "";
    }
  }

  function norm(value) {
    if (value === "EMPTY") return "";
    return value || "";
  }

  function renderRows(rows) {
    if (!gridEl) return;
    gridEl.innerHTML = "";
    if (!rows || rows.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No strategies found.";
      gridEl.appendChild(empty);
      return;
    }

    rows.forEach((r) => {
      const card = document.createElement("article");
      card.className = `card ${r.enabled ? "enabled" : "disabled"}`;

      const title = document.createElement("h2");
      title.className = "name";
      title.textContent = r.strategy_name || "(unnamed)";

      const sub = document.createElement("div");
      sub.className = "meta";
      const instrument = norm(r.instrument);
      const connection = norm(r.connection);
      sub.textContent = [
        instrument ? `Instrument: ${instrument}` : null,
        connection ? `Connection: ${connection}` : null
      ]
        .filter(Boolean)
        .join(" · ");

      const tag = document.createElement("div");
      tag.className = "state-tag";
      tag.textContent = r.enabled ? "Enabled" : "Disabled";

      const ts = document.createElement("div");
      ts.className = "updated-at";
      ts.textContent = `Updated: ${fmtTime(r.updated_at)}`;

      card.appendChild(title);
      card.appendChild(tag);
      card.appendChild(sub);
      card.appendChild(ts);
      gridEl.appendChild(card);
    });
  }

  async function main() {
    if (missing.length) {
      setStatus(`Missing config: ${missing.join(", ")}`, true);
      return;
    }
    if (!window.supabase || !window.supabase.createClient) {
      setStatus("Supabase library not loaded.", true);
      return;
    }

    const client = window.supabase.createClient(cfg.url, cfg.anonKey);

    async function tick() {
      setBusy(true);
      try {
        const { data, error } = await client
          .from(table)
          .select("strategy_name,instrument,enabled,connection,updated_at")
          .order("strategy_name", { ascending: true })
          .order("instrument", { ascending: true });
        if (error) throw error;

        renderRows(data || []);
        setStatus("");
        if (lastUpdatedEl) {
          lastUpdatedEl.textContent = `Last refresh: ${fmtTime(new Date().toISOString())}`;
        }
      } catch (err) {
        setStatus(`Error loading status: ${err.message || err}`, true);
      } finally {
        setBusy(false);
      }
    }

    await tick();
    setInterval(tick, pollMs);
  }

  // Kick off after DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();


