// app.js

async function api(path, options = {}) {
  const base = (window.API_BASE || '').replace(/\/+$/, '');
  if (!base) throw new Error('API_BASE 未设置，请检查 config.js');
  const url = `${base}/${String(path).replace(/^\/+/, '')}`;

  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`API ${res.status}: ${text}`);
  }
  try { return await res.json(); } catch { return {}; }
}

// —— 两个测试函数（可绑在按钮上）——

async function testHealth() {
  try {
    const data = await api('health'); // GET /health
    console.log('health:', data);
    showOutput(data);
  } catch (e) {
    showError(e);
  }
}

async function testStart() {
  try {
    // ⚠️ 把 cards 改成你后端要求的字段名（如 hand / holeCards）
    const body = { cards: ['As', 'Kd'] };
    const data = await api('start', { method: 'POST', body: JSON.stringify(body) });
    console.log('start:', data);
    showOutput(data);
  } catch (e) {
    showError(e);
  }
}

// —— 简单的输出/报错渲染 ——
// 你已有 UI 就复用；没有就用下面这段
function showOutput(obj) {
  const el = document.getElementById('output');
  if (el) el.textContent = JSON.stringify(obj, null, 2);
}
function showError(err) {
  const el = document.getElementById('output');
  if (el) el.textContent = `错误：${err.message || err}`;
}

// 把函数挂到 window，方便按钮 onclick 调用
window.testHealth = testHealth;
window.testStart  = testStart;
