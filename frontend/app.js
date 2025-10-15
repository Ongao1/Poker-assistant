(function () {
  // ---------- 小工具 ----------
  const $ = (s) => document.querySelector(s);
  const progressPanel = $("#progressPanel");
  const bar = $("#bar"),
    pctText = $("#pctText"),
    stageText = $("#stageText"),
    etaText = $("#etaText"),
    streetText = $("#streetText");
  const equityText = $("#equity-text"),
    equityBar = $("#equity-bar");
  const adviceBox = $("#advice"),
    oddsText = $("#odds-text");

  // 关键：默认同域 /api（由 Vercel 反代到 Render）
  const API_BASE = (typeof window.API_BASE === "string" && window.API_BASE.trim()) || "/api";

  let es = null,
    currentTask = null;

  function secsToHHMMSS(s) {
    if (s == null) return "";
    s = Math.max(0, s | 0);
    const h = (s / 3600) | 0,
      m = ((s % 3600) / 60) | 0,
      sec = s % 60;
    if (h > 0) return `${h}h ${m}m ${sec}s`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
  }

  function calcPotOdds(callAmt, potAmt) {
    if (!callAmt || !potAmt) return null;
    const c = Number(callAmt),
      p = Number(potAmt);
    if (!(c > 0) || !(p >= 0)) return null;
    return c / (c + p); // 需要的最小胜率
  }

  function pickStreet({ flop, turn, river }) {
    if (river) return "River";
    if (turn && flop) return "Turn";
    if (flop) return "Flop";
    return null;
  }

  // 统一 fetch（带超时 & 更清晰的错误）
  async function apiFetch(path, { method = "GET", body, headers } = {}, timeoutMs = 20000) {
    const url = path.startsWith("http") ? path : `${API_BASE}${path}`;
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        method,
        body,
        headers,
        signal: ctl.signal,
      });
      clearTimeout(t);
      return res;
    } catch (e) {
      clearTimeout(t);
      throw new Error(`网络错误：${e.message}（请求 ${url}）`);
    }
  }

  async function start() {
    // 读取输入
    const get = (id) => (document.getElementById(id)?.value || "").trim();
    const hero = [get("hole1"), get("hole2")].filter(Boolean).join(" ");
    const flop = [get("b1"), get("b2"), get("b3")].filter(Boolean).join(" ");
    const turn = get("b4");
    const river = get("b5");
    const pot = get("pot");
    const call = get("call");

    // 组装后端字段
    const fd = new FormData();
    fd.append("hero", hero);
    if (flop) fd.append("flop", flop);
    if (turn) fd.append("turn", turn);
    if (river) fd.append("river", river);
    fd.append("villains", "2");
    if (pot) fd.append("pot_bb", pot);

    const street = pickStreet({ flop, turn, river });
    if (call && street) {
      if (street === "Flop") fd.append("call_flop", call);
      if (street === "Turn") fd.append("call_turn", call);
      if (street === "River") fd.append("call_river", call);
    }

    // UI 重置
    progressPanel && (progressPanel.style.display = "block");
    bar && (bar.style.width = "0%");
    pctText && (pctText.textContent = "0%");
    stageText && (stageText.textContent = "排队中…");
    etaText && (etaText.textContent = "");
    streetText && (streetText.textContent = "");
    equityText && (equityText.textContent = "—");
    equityBar && (equityBar.style.width = "0%");
    adviceBox && (adviceBox.textContent = "// 等待后端…");

    // 本地计算 Pot Odds（可对齐后端建议）
    const po = calcPotOdds(call, pot);
    oddsText && (oddsText.textContent = po != null ? `${(po * 100).toFixed(2)}%` : "—");

    // 调后端 /start（同域 /api/start）
    let resp;
    try {
      resp = await apiFetch("/start", { method: "POST", body: fd });
    } catch (e) {
      stageText && (stageText.textContent = e.message);
      console.error(e);
      return;
    }

    if (!resp.ok) {
      let msg = `启动失败：HTTP ${resp.status}`;
      try {
        const err = await resp.json();
        if (err && err.error) msg += ` → ${err.error}`;
      } catch (_) {}
      stageText && (stageText.textContent = msg);
      return;
    }

    const data = await resp.json();
    const { task_id } = data || {};
    if (!task_id) {
      stageText && (stageText.textContent = "启动失败：后端未返回 task_id");
      return;
    }
    currentTask = task_id;

    // 订阅 SSE：/api/stream/:task_id  → 反代到后端 /stream/:task_id
    try {
      if (es) es.close();
      const sseUrl = `${location.origin}${API_BASE}/stream/${encodeURIComponent(task_id)}`;
      es = new EventSource(sseUrl);

      es.onopen = () => {
        stageText && (stageText.textContent = "计算中…");
      };

      es.onerror = () => {
        stageText && (stageText.textContent = "连接中断，重试中…");
      };

      es.onmessage = (evt) => {
        let s = {};
        try {
          s = JSON.parse(evt.data || "{}");
        } catch (e) {
          console.warn("SSE 数据解析失败：", evt.data);
          return;
        }

        const pct = Number(s.pct || 0);
        bar && (bar.style.width = pct + "%");
        pctText && (pctText.textContent = pct + "%");
        stageText && (stageText.textContent = s.stage || "");
        etaText && (etaText.textContent = s.eta != null ? "ETA " + secsToHHMMSS(s.eta) : "");
        streetText &&
          (streetText.textContent = s.detail && s.detail.street ? "当前街：" + s.detail.street : "");

        if (Array.isArray(s.results) && s.results.length) {
          const last = s.results[s.results.length - 1];

          if (equityText) {
            const eq = Number(last.equity || 0);
            if (isFinite(eq)) {
              const pctStr = (eq * 100).toFixed(2) + "%";
              equityText.textContent = pctStr;
              equityBar && (equityBar.style.width = pctStr);
            } else {
              equityText.textContent = "—";
              equityBar && (equityBar.style.width = "0%");
            }
          }

          if (adviceBox) {
            const html =
              (last.advice_text || "").replaceAll("\n", "<br/>") || "// 暂无建议";
            adviceBox.innerHTML = html;
          }
        }

        if (s.done) {
          es && es.close();
          stageText && (stageText.textContent = "完成");
          etaText && (etaText.textContent = "");
          bar && (bar.style.width = "100%");
          pctText && (pctText.textContent = "100%");
        }
      };
    } catch (e) {
      stageText && (stageText.textContent = "SSE 建立失败：" + e.message);
      console.error(e);
    }
  }

  // 提供给 index.html 调用
  window.__RUN_SIM__ = start;
})();
