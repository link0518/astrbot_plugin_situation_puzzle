const bridge = window.AstrBotPluginView;

const state = { days: 1, paused: false, subscription: null, maxLines: 200 };
const toastEl = document.getElementById("toast");

function toast(message, kind = "ok") {
  toastEl.textContent = message;
  toastEl.className = `toast ${kind}`;
  toastEl.hidden = false;
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => {
    toastEl.hidden = true;
  }, 4000);
}

function node(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") element.className = value;
    else if (key === "text") element.textContent = value;
    else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null) element.setAttribute(key, value);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    element.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return element;
}

function statCard(label, value, unit = "") {
  return node("div", { class: "stat" },
    node("div", { class: "k", text: label }),
    node("div", { class: "v" }, String(value), unit ? node("span", { class: "u", text: unit }) : null),
  );
}

const VERDICT_LABELS = { yes: "是", no: "否", irrelevant: "无关", uncertain: "无法确定", error: "失败" };
const ERROR_LABELS = {
  network: "网络",
  upstream: "上游",
  rate_limited: "限流",
  auth: "鉴权",
  protocol: "协议",
  timeout: "超时",
  error: "其他",
};

function renderBars(container, entries, labels, tone) {
  container.innerHTML = "";
  const items = Object.entries(entries || {}).filter(([, count]) => count > 0);
  if (!items.length) {
    container.appendChild(node("div", { class: "empty", text: "暂无数据" }));
    return;
  }
  const max = Math.max(...items.map(([, count]) => count));
  for (const [key, count] of items.sort((a, b) => b[1] - a[1])) {
    const row = node("div", { class: "bar-row" },
      node("span", { text: labels[key] || key }),
      node("div", { class: `bar ${tone || ""}` }, node("span", { style: `width:${Math.round((count / max) * 100)}%` })),
      node("span", { text: String(count), style: "text-align:right" }),
    );
    container.appendChild(row);
  }
}

function renderStats(data) {
  const stats = document.getElementById("stats");
  stats.innerHTML = "";
  const asks = data.asks || 0;
  const solved = data.solved || 0;
  const rate = asks ? Math.round((solved / Math.max(1, data.solves || asks)) * 100) : 0;
  stats.appendChild(statCard("对局", data.rooms || 0));
  stats.appendChild(statCard("提问", asks));
  stats.appendChild(statCard("破解", solved));
  stats.appendChild(statCard("破解率", rate, "%"));
  stats.appendChild(statCard("平均耗时", data.avg_latency_ms || 0, "ms"));
  stats.appendChild(statCard("P95 耗时", data.p95_latency_ms || 0, "ms"));
  stats.appendChild(statCard("活跃房间", data.active_rooms || 0));
  stats.appendChild(statCard("题库", data.puzzle_enabled || 0));

  document.getElementById("metaLine").textContent =
    `引擎 ${data.engine} · 阈值 ${Number(data.threshold).toFixed(2)} · 今日已开局 ${data.daily_used} · 题库 ${data.puzzle_total} 题`;
  renderBars(document.getElementById("verdictBars"), data.verdicts, VERDICT_LABELS);
  renderBars(document.getElementById("errorBars"), data.errors, ERROR_LABELS, "danger");
}

function renderRooms(data) {
  const wrap = document.getElementById("rooms");
  wrap.innerHTML = "";
  const rooms = data.rooms || [];
  document.getElementById("roomSummary").textContent = rooms.length ? `${rooms.length} 个进行中` : "当前没有进行中的对局";
  if (!rooms.length) {
    wrap.appendChild(node("div", { class: "empty", text: "空闲中。" }));
    return;
  }
  const table = node("table", { class: "list" });
  table.appendChild(node("tr", {},
    node("th", { text: "会话" }),
    node("th", { text: "题目" }),
    node("th", { text: "回合" }),
    node("th", { text: "提示" }),
    node("th", { text: "人数" }),
    node("th", { text: "闲置" }),
  ));
  for (const room of rooms) {
    const idle = room.idle_seconds < 60 ? `${room.idle_seconds}s` : `${Math.floor(room.idle_seconds / 60)}min`;
    table.appendChild(node("tr", {},
      node("td", { class: "sub", text: room.umo }),
      node("td", { text: room.title || room.puzzle_id }),
      node("td", { text: String(room.rounds) }),
      node("td", { text: `${room.hints_used}/${room.hint_total}` }),
      node("td", { text: String(room.participants) }),
      node("td", { text: idle }),
    ));
  }
  wrap.appendChild(table);
}

async function loadStats() {
  const data = await bridge.apiGet("stats", { days: state.days });
  renderStats(data);
}

async function loadRooms() {
  const data = await bridge.apiGet("rooms");
  renderRooms(data);
}

function appendLog(event) {
  if (state.paused) return;
  const log = document.getElementById("log");
  const time = new Date((event.ts || 0) * 1000).toLocaleTimeString();
  const line = node("div", { class: `log-line ${event.level || "info"}` },
    node("span", { class: "t", text: time }),
    node("span", { class: "m", text: event.message || "" }),
    event.confidence !== undefined ? node("span", { class: "t", text: `c=${event.confidence}` }) : null,
    event.latency_ms ? node("span", { class: "t", text: `${event.latency_ms}ms` }) : null,
  );
  log.appendChild(line);
  while (log.children.length > state.maxLines) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
}

async function loadInitialLogs() {
  const data = await bridge.apiGet("logs", { limit: 40 });
  document.getElementById("log").innerHTML = "";
  for (const item of data.logs || []) appendLog(item);
}

async function subscribe() {
  try {
    state.subscription = await bridge.subscribeSSE("events", {
      onOpen() {
        document.getElementById("sseState").textContent = "已连接";
      },
      onMessage(event) {
        if (event.parsed && typeof event.parsed === "object") appendLog(event.parsed);
      },
      onError() {
        document.getElementById("sseState").textContent = "连接中断，点击刷新重连";
      },
    });
  } catch (error) {
    document.getElementById("sseState").textContent = `订阅失败：${error.message}`;
  }
}

async function main() {
  if (!bridge) {
    document.getElementById("metaLine").textContent = "未检测到插件页面桥接，请从 AstrBot 插件详情页打开。";
    return;
  }
  await bridge.ready();

  document.getElementById("days").addEventListener("change", async (event) => {
    state.days = Number(event.target.value) || 1;
    await loadStats();
  });
  document.getElementById("refreshBtn").addEventListener("click", async () => {
    try {
      await Promise.all([loadStats(), loadRooms()]);
      toast("已刷新");
    } catch (error) {
      toast(`刷新失败：${error.message}`, "bad");
    }
  });
  document.getElementById("pauseBtn").addEventListener("click", (event) => {
    state.paused = !state.paused;
    event.target.textContent = state.paused ? "继续" : "暂停";
  });

  try {
    await Promise.all([loadStats(), loadRooms(), loadInitialLogs()]);
  } catch (error) {
    document.getElementById("metaLine").textContent = `读取失败：${error.message}`;
    return;
  }
  await subscribe();

  window.setInterval(() => {
    loadStats().catch(() => {});
    loadRooms().catch(() => {});
  }, 30000);

  window.addEventListener("beforeunload", () => {
    if (state.subscription) bridge.unsubscribeSSE(state.subscription);
  });
}

main();
