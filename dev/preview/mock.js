// 本地面板预览：用假 bridge + 假数据把 views/ 跑起来，不依赖 AstrBot。
const params = new URLSearchParams(location.search);
const view = params.get("view") || "settings";
const theme = params.get("theme") || "dark";
const root = new URL("../../", location.href);
const viewDir = new URL(`views/${view}/`, root);

document.documentElement.dataset.theme = theme;

const schema = await fetch(new URL("_conf_schema.json", root)).then((r) => r.json());
const seed = await fetch(new URL("resources/library.seed.json", root)).then((r) => r.json());

const library = seed.map((item, index) => ({
  ...item,
  enabled: true,
  play_count: (index * 7) % 40,
  solved_count: (index * 3) % 12,
}));

const values = defaultsFromSchema(schema);
values.judge.jev_api_key = "";

function defaultsFromSchema(node) {
  const out = {};
  for (const [key, def] of Object.entries(node)) {
    if (def.type === "object") {
      out[key] = defaultsFromSchema(def.items || {});
    } else {
      out[key] = def.default;
    }
  }
  return out;
}

function merge(target, patch) {
  for (const [key, value] of Object.entries(patch || {})) {
    if (value && typeof value === "object" && !Array.isArray(value) && target[key] && typeof target[key] === "object") {
      merge(target[key], value);
    } else {
      target[key] = value;
    }
  }
  return target;
}

const providers = [
  { id: "deepseek-v3", label: "deepseek-v3 · deepseek-chat" },
  { id: "gpt-4o-mini", label: "gpt-4o-mini · gpt-4o-mini" },
];

const logs = [
  { ts: Math.floor(Date.now() / 1000) - 40, level: "info", message: "开局：终点线", umo: "qq:Group:1024", puzzle: "real-finish-line" },
  { ts: Math.floor(Date.now() / 1000) - 32, level: "judge", message: "爸爸已经去世了吗？ → 是", confidence: 0.93, latency_ms: 612 },
  { ts: Math.floor(Date.now() / 1000) - 26, level: "judge", message: "他最后赢了吗？ → 否", confidence: 0.88, latency_ms: 705 },
  { ts: Math.floor(Date.now() / 1000) - 20, level: "judge", message: "他喜欢吃苹果吗？ → 无关", confidence: 0.71, latency_ms: 668 },
  { ts: Math.floor(Date.now() / 1000) - 12, level: "error", message: "判题失败：rate_limited", detail: "429" },
  { ts: Math.floor(Date.now() / 1000) - 6, level: "solve", message: "破解成功：终点线", umo: "qq:Group:1024" },
];

const rooms = [
  {
    umo: "qq:Group:1024", room_id: "default", puzzle_id: "real-finish-line", title: "终点线",
    state: "playing", rounds: 7, hints_used: 1, hint_total: 3, participants: 3,
    owner_id: "10001", started_at: Math.floor(Date.now() / 1000) - 620,
    updated_at: Math.floor(Date.now() / 1000) - 12, idle_seconds: 12,
  },
  {
    umo: "telegram:Group:-1002", room_id: "default", puzzle_id: "real-five-seconds", title: "54321",
    state: "playing", rounds: 2, hints_used: 0, hint_total: 3, participants: 1,
    owner_id: "20002", started_at: Math.floor(Date.now() / 1000) - 180,
    updated_at: Math.floor(Date.now() / 1000) - 95, idle_seconds: 95,
  },
];

const stats = {
  asks: 128, solves: 19, solved: 12, close: 5, uncertain: 7, rooms: 23,
  avg_latency_ms: 731, p95_latency_ms: 1480, active_rooms: rooms.length,
  puzzle_total: library.length, puzzle_enabled: library.length,
  daily_used: 23, engine: "jev", threshold: 0.5, days: 1,
  verdicts: { yes: 61, no: 43, irrelevant: 17, uncertain: 7 },
  errors: { rate_limited: 3, timeout: 2, protocol: 1 },
};

let sseTimer = null;

const bridge = {
  ready: async () => ({ pluginName: "astrbot_plugin_situation_puzzle", pageName: view, locale: "zh-CN", isDark: theme === "dark" }),
  getContext: () => ({ locale: "zh-CN", isDark: theme === "dark" }),
  t: (key, fallback) => fallback,
  async apiGet(endpoint, query = {}) {
    if (endpoint === "settings") {
      return {
        schema,
        values,
        meta: {
          plugin_name: "astrbot_plugin_situation_puzzle",
          puzzle_total: library.length,
          difficulty: { easy: 130, medium: 194, hard: 26 },
          providers,
          default_provider: "deepseek-v3",
          seed_version: "2026-10-19.1",
          engine: values.judge.engine,
        },
      };
    }
    if (endpoint === "library/list") return listLibrary(query);
    if (endpoint === "stats") return { ...stats, days: Number(query.days) || 1 };
    if (endpoint === "rooms") return { rooms };
    if (endpoint === "logs") return { logs: logs.slice(-(Number(query.limit) || 40)) };
    throw new Error(`未知接口：${endpoint}`);
  },
  async apiPost(endpoint, body = {}) {
    if (endpoint === "settings/save") {
      merge(values, body);
      return { saved: true, errors: [], values, meta: { engine: values.judge.engine } };
    }
    if (endpoint === "engine/test") {
      await wait(650);
      return {
        ok: true, verdict: "yes", label: "是", confidence: 0.86, threshold: values.judge.threshold,
        latency_ms: 742, engine: values.judge.engine, degraded: false,
      };
    }
    if (endpoint === "engine/calibrate") {
      await wait(1400);
      const items = [
        { question: "爸爸已经去世了吗？", expect: "是", verdict: "是", confidence: 0.93, latency_ms: 640, ok: true, hit: true },
        { question: "那个东西是一台游戏机吗？", expect: "是", verdict: "是", confidence: 0.88, latency_ms: 702, ok: true, hit: true },
        { question: "幽灵车手是爸爸留下的记录吗？", expect: "是", verdict: "是", confidence: 0.81, latency_ms: 688, ok: true, hit: true },
        { question: "他最后赢了比赛吗？", expect: "否", verdict: "否", confidence: 0.9, latency_ms: 655, ok: true, hit: true },
        { question: "故事发生在船上吗？", expect: "否", verdict: "否", confidence: 0.84, latency_ms: 721, ok: true, hit: true },
        { question: "他喜欢吃苹果吗？", expect: "无关", verdict: "无法确定", confidence: 0.42, latency_ms: 690, ok: true, hit: false, degraded: true },
      ];
      return { ok: true, puzzle: { id: "real-finish-line", title: "终点线" }, engine: values.judge.engine, threshold: values.judge.threshold, scored: 6, hits: 5, accuracy: 83.3, items };
    }
    if (endpoint === "library/upsert") {
      const index = library.findIndex((item) => item.id === body.id);
      if (index >= 0) library[index] = { ...library[index], ...body };
      else library.unshift({ ...body, id: body.id || `custom-${Date.now()}`, play_count: 0, solved_count: 0 });
      return { saved: true, id: body.id };
    }
    if (endpoint === "library/lint") {
      const issues = [];
      for (const item of library) {
        if ((item.hints || []).length < 3) issues.push({ id: item.id, level: "info", message: "提示不足 3 条" });
        if ((item.answer || "").length < 20) issues.push({ id: item.id, level: "warn", message: "汤底过短" });
        if (issues.length >= 40) break;
      }
      return { total: library.length, issues, count: issues.length };
    }
    if (endpoint === "library/sync") return { ok: true, imported: library.length, total: library.length };
    throw new Error(`未知接口：${endpoint}`);
  },
  async upload() {
    await wait(300);
    return { imported: 0, skipped: 0, total: 0 };
  },
  async download() {
    await wait(300);
    return { filename: "library.json" };
  },
  async subscribeSSE(endpoint, handlers = {}) {
    if (endpoint !== "events") throw new Error(`未知 SSE：${endpoint}`);
    handlers.onOpen?.();
    sseTimer = window.setInterval(() => {
      const sample = [
        { level: "judge", message: "她真的死在船上吗？ → 否", confidence: 0.79, latency_ms: 664 },
        { level: "judge", message: "凶手是熟人吗？ → 是", confidence: 0.87, latency_ms: 712 },
        { level: "info", message: "提示 2/3 已解锁", umo: "qq:Group:1024" },
      ];
      const item = sample[Math.floor(Math.random() * sample.length)];
      handlers.onMessage?.({ parsed: { ts: Math.floor(Date.now() / 1000), ...item } });
    }, 4200);
    return 1;
  },
  async unsubscribeSSE() {
    window.clearInterval(sseTimer);
  },
};

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function listLibrary(query) {
  const page = Math.max(1, Number(query.page) || 1);
  const size = Math.min(200, Math.max(1, Number(query.size) || 20));
  const keyword = (query.q || "").trim();
  let items = library;
  if (keyword) {
    items = items.filter((item) =>
      [item.id, item.title, item.story, item.answer].some((field) => (field || "").includes(keyword)),
    );
  }
  if (query.difficulty) items = items.filter((item) => item.difficulty === query.difficulty);
  if (query.category) items = items.filter((item) => item.category === query.category);
  if (query.enabled) items = items.filter((item) => String(item.enabled ? 1 : 0) === String(query.enabled));
  const total = items.length;
  return { total, page, size, items: items.slice((page - 1) * size, page * size) };
}

window.AstrBotPluginView = bridge;
window.AstrBotPluginPage = bridge;

const html = await fetch(new URL("index.html", viewDir)).then((r) => r.text());
const doc = new DOMParser().parseFromString(html, "text/html");

for (const link of doc.querySelectorAll('link[rel="stylesheet"]')) {
  const node = document.createElement("link");
  node.rel = "stylesheet";
  node.href = new URL(link.getAttribute("href"), viewDir).href;
  document.head.appendChild(node);
}

document.title = `面板预览 · ${doc.title}`;
document.body.innerHTML = doc.body.innerHTML;

for (const script of doc.querySelectorAll("script[src]")) {
  const node = document.createElement("script");
  node.type = "module";
  node.src = new URL(script.getAttribute("src"), viewDir).href;
  document.body.appendChild(node);
}

const nav = document.createElement("nav");
nav.innerHTML = `
  <style>
    nav { position: fixed; right: 16px; bottom: 16px; display: flex; gap: 6px; z-index: 99;
          background: rgba(30,30,29,.92); border: 1px solid rgba(255,255,255,.18); border-radius: 999px; padding: 6px; }
    nav a { color: #d8d8d2; text-decoration: none; font-size: 12px; padding: 4px 12px; border-radius: 999px;
            font-family: system-ui, "Microsoft YaHei", sans-serif; }
    nav a.on { background: #5dcaa5; color: #06362c; }
  </style>
  ${["settings", "library", "dashboard"].map((name) => `<a href="?view=${name}" class="${name === view ? "on" : ""}">${name}</a>`).join("")}
`;
document.body.appendChild(nav);
