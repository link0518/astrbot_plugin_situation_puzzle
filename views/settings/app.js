const bridge = window.AstrBotPluginView;
const SECTIONS = ["judge", "basic", "game", "limit", "message"];

const OPTION_LABELS = {
  jev: "Jev SystemOne（原版协议）",
  provider: "AstrBot 大模型（provider）",
  zh: "中文",
  en: "English",
  group: "一个群一局",
  user: "每人独立进度",
  random: "随机",
  easy: "简单",
  medium: "中等",
  hard: "困难",
  all: "所有人",
  admin: "仅管理员",
  both: "两种都认（/汤 · 汤）",
  slash: "仅斜杠（/汤 提示）",
  text: "仅文字（汤 提示）",
};

const state = { schema: {}, values: {}, meta: {}, loaded: false };

const formRoot = document.getElementById("form");
const toastEl = document.getElementById("toast");
const engineBadge = document.getElementById("engineBadge");
const metaLine = document.getElementById("metaLine");

function toast(message, kind = "ok") {
  toastEl.textContent = message;
  toastEl.className = `toast ${kind}`;
  toastEl.hidden = false;
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => {
    toastEl.hidden = true;
  }, 4200);
}

function node(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") element.className = value;
    else if (key === "text") element.textContent = value;
    else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else element.setAttribute(key, value);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    element.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return element;
}

function getValue(path) {
  return path.split(".").reduce((acc, key) => (acc && typeof acc === "object" ? acc[key] : undefined), state.values);
}

function setValue(target, path, value) {
  const parts = path.split(".");
  let cursor = target;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) cursor[part] = value;
    else {
      cursor[part] = cursor[part] || {};
      cursor = cursor[part];
    }
  });
}

function secretConfigured(path) {
  const flags = state.values._secrets || {};
  return Boolean(flags[path]);
}

function engineKind() {
  return (getValue("judge.engine") || "jev").toString();
}

function makeControl(path, def) {
  const id = `f-${path.replace(/\./g, "-")}`;
  const type = def.type;
  const value = getValue(path);

  if (type === "bool") {
    const input = node("input", { type: "checkbox", id });
    input.checked = Boolean(value);
    input.dataset.path = path;
    input.dataset.kind = "bool";
    return { control: node("div", { class: "switch" }, input, node("span", { class: "sub", text: input.checked ? "开启" : "关闭" })), id };
  }

  if (type === "text") {
    const area = node("textarea", { id, rows: "5" });
    area.value = value ?? def.default ?? "";
    area.dataset.path = path;
    area.dataset.kind = "string";
    return { control: area, id };
  }

  if (path === "judge.threshold") {
    const range = node("input", { type: "range", min: "0.3", max: "0.9", step: "0.01", id });
    range.value = value ?? def.default ?? 0.5;
    range.dataset.path = path;
    range.dataset.kind = "number";
    const readout = node("span", { class: "range-value", text: Number(range.value).toFixed(2) });
    range.addEventListener("input", () => {
      readout.textContent = Number(range.value).toFixed(2);
    });
    return { control: node("div", { class: "range-row" }, range, readout), id };
  }

  if (def._special === "select_provider") {
    const select = node("select", { id });
    const providers = state.meta.providers || [];
    select.appendChild(node("option", { value: "", text: "跟随当前会话模型" }));
    for (const provider of providers) {
      select.appendChild(node("option", { value: provider.id, text: provider.label || provider.id }));
    }
    if (value && !providers.some((item) => item.id === value)) {
      select.appendChild(node("option", { value, text: `${value}（当前配置）` }));
    }
    select.value = value || "";
    select.dataset.path = path;
    select.dataset.kind = "string";
    return { control: select, id };
  }

  if (def.options && Array.isArray(def.options)) {
    const select = node("select", { id });
    for (const option of def.options) {
      select.appendChild(node("option", { value: option, text: OPTION_LABELS[option] || option }));
    }
    select.value = value ?? def.default ?? "";
    select.dataset.path = path;
    select.dataset.kind = "string";
    return { control: select, id };
  }

  if (type === "int" || type === "float") {
    const input = node("input", {
      type: "number",
      id,
      step: type === "float" ? "0.01" : "1",
    });
    input.value = value ?? def.default ?? 0;
    input.dataset.path = path;
    input.dataset.kind = "number";
    return { control: input, id };
  }

  const input = node("input", { type: def.secret ? "password" : "text", id });
  input.value = def.secret ? "" : value ?? def.default ?? "";
  input.dataset.path = path;
  input.dataset.kind = "string";
  if (def.secret) {
    input.placeholder = secretConfigured(path) ? "已配置，留空表示不修改" : "未配置";
  }
  return { control: input, id };
}

function makeField(path, def) {
  const { control, id } = makeControl(path, def);
  const label = node("label", { class: "field-label", for: id }, def.description || path);
  const wrapper = node("div", { class: "field" }, label, control);
  if (def._special === "select_provider" || path.startsWith("judge.jev_")) {
    wrapper.dataset.engine = path === "judge.provider_id" ? "provider" : "jev";
  }
  if (def.type === "text" || path === "judge.jev_base_url") wrapper.classList.add("wide");
  if (def.hint) wrapper.appendChild(node("span", { class: "field-hint", text: def.hint }));
  if (def.type === "text") {
    const chips = node("div", { class: "chips" });
    for (const name of ["{title}", "{story}", "{answer}", "{host}", "{question}", "{verdict}", "{source}", "{rounds}"]) {
      chips.appendChild(node("span", { class: "chip", text: name }));
    }
    wrapper.appendChild(chips);
  }
  return wrapper;
}

function applyEngineVisibility() {
  const kind = engineKind();
  for (const field of formRoot.querySelectorAll("[data-engine]")) {
    field.hidden = field.dataset.engine !== kind;
  }
  engineBadge.textContent = kind === "provider" ? "引擎：AstrBot 大模型" : "引擎：Jev SystemOne";
}

function renderForm() {
  formRoot.innerHTML = "";
  const { schema } = state;

  if (schema.enabled) {
    const card = node("section", { class: "card" });
    card.appendChild(makeField("enabled", schema.enabled));
    formRoot.appendChild(card);
  }

  const keys = SECTIONS.filter((key) => schema[key] && schema[key].type === "object");
  for (const key of Object.keys(schema)) {
    if (key !== "enabled" && schema[key]?.type === "object" && !keys.includes(key)) keys.push(key);
  }

  for (const key of keys) {
    const section = schema[key];
    const card = node("section", { class: "card" });
    card.appendChild(node("div", { class: "card-head" }, node("h2", { text: section.description || key })));
    const grid = node("div", { class: "grid" });
    for (const [fieldKey, def] of Object.entries(section.items || {})) {
      grid.appendChild(makeField(`${key}.${fieldKey}`, def));
    }
    card.appendChild(grid);
    formRoot.appendChild(card);
  }

  const engineSelect = formRoot.querySelector('[data-path="judge.engine"]');
  if (engineSelect) engineSelect.addEventListener("change", applyEngineVisibility);
  applyEngineVisibility();

  metaLine.textContent = `题库 ${state.meta.puzzle_total ?? 0} 题 · 阈值 ${Number(getValue("judge.threshold") ?? 0.5).toFixed(2)} · ${state.meta.engine === "provider" ? "大模型判题" : "Jev 判题"}`;
}

function collect() {
  const patch = {};
  for (const element of formRoot.querySelectorAll("[data-path]")) {
    const path = element.dataset.path;
    let value;
    if (element.dataset.kind === "bool") value = element.checked;
    else if (element.dataset.kind === "number") value = Number(element.value);
    else value = element.value;
    setValue(patch, path, value);
  }
  return patch;
}

async function load() {
  const data = await bridge.apiGet("settings");
  state.schema = data.schema || {};
  state.values = data.values || {};
  state.meta = data.meta || {};
  state.loaded = true;
  renderForm();
}

async function save() {
  const button = document.getElementById("saveBtn");
  button.disabled = true;
  try {
    const result = await bridge.apiPost("settings/save", collect());
    if (result.errors && result.errors.length) {
      toast(`未保存：${result.errors.join("；")}`, "bad");
      return;
    }
    state.values = result.values || state.values;
    state.meta.engine = result.meta?.engine || state.meta.engine;
    renderForm();
    toast("已保存，配置立即生效。");
  } catch (error) {
    toast(`保存失败：${error.message}`, "bad");
  } finally {
    button.disabled = false;
  }
}

function verdictClass(result) {
  if (!result.ok) return "v-error";
  return { yes: "v-yes", no: "v-no", irrelevant: "v-irrelevant" }[result.verdict] || "v-uncertain";
}

function renderTestResult(result) {
  const box = document.getElementById("testResult");
  box.hidden = false;
  box.innerHTML = "";

  if (!result.ok) {
    box.appendChild(node("div", { class: "verdict-line" },
      node("span", { class: "verdict v-error", text: result.error_class || "失败" }),
      node("span", { class: "sub", text: result.message || "未知错误" }),
    ));
    return;
  }

  const percent = Math.round((result.confidence || 0) * 100);
  box.appendChild(node("div", { class: "verdict-line" },
    node("span", { class: `verdict ${verdictClass(result)}`, text: result.label || result.verdict }),
    node("span", { class: "sub", text: `置信度 ${Number(result.confidence).toFixed(2)}` }),
    node("span", { class: "sub", text: `阈值 ${Number(result.threshold).toFixed(2)}` }),
    node("span", { class: "sub", text: `耗时 ${result.latency_ms} ms` }),
    node("span", { class: "sub", text: `引擎 ${result.engine}` }),
  ));
  box.appendChild(node("div", { class: "meter" }, node("span", { style: `width:${percent}%` })));
  if (result.degraded) {
    box.appendChild(node("p", { class: "sub", text: "置信度低于阈值，已按「无法确定」处理 —— 这是预期行为，不会硬猜。" }));
  }
}

function renderCalibration(data) {
  const box = document.getElementById("calibrateWrap");
  box.hidden = false;
  box.innerHTML = "";
  box.appendChild(node("div", { class: "verdict-line" },
    node("span", { class: "verdict v-uncertain", text: `命中 ${data.hits}/${data.scored}` }),
    node("span", { class: "sub", text: `命中率 ${data.accuracy}%` }),
    node("span", { class: "sub", text: `引擎 ${data.engine} · 阈值 ${data.threshold}` }),
  ));

  const table = node("table");
  table.appendChild(node("tr", {},
    node("th", { text: "问题" }),
    node("th", { text: "期望" }),
    node("th", { text: "判定" }),
    node("th", { text: "置信度" }),
    node("th", { text: "耗时" }),
  ));
  for (const item of data.items || []) {
    table.appendChild(node("tr", {},
      node("td", { text: item.question }),
      node("td", { text: item.expect || "—" }),
      node("td", { class: item.expect ? (item.hit ? "hit" : "miss") : "", text: item.verdict || "—" }),
      node("td", { text: item.ok ? Number(item.confidence).toFixed(2) : "—" }),
      node("td", { text: item.ok ? `${item.latency_ms} ms` : "失败" }),
    ));
  }
  box.appendChild(table);
}

async function runTest() {
  const question = document.getElementById("testQuestion").value.trim();
  if (!question) {
    toast("先写一个测试问题", "bad");
    return;
  }
  const button = document.getElementById("testBtn");
  button.disabled = true;
  button.textContent = "判定中…";
  try {
    renderTestResult(await bridge.apiPost("engine/test", { question }));
  } catch (error) {
    toast(`试判失败：${error.message}`, "bad");
  } finally {
    button.disabled = false;
    button.textContent = "试判";
  }
}

async function runCalibration() {
  const button = document.getElementById("calibrateBtn");
  button.disabled = true;
  button.textContent = "校准中…";
  try {
    renderCalibration(await bridge.apiPost("engine/calibrate", {}));
  } catch (error) {
    toast(`校准失败：${error.message}`, "bad");
  } finally {
    button.disabled = false;
    button.textContent = "跑一组校准";
  }
}

async function main() {
  if (!bridge) {
    metaLine.textContent = "未检测到插件页面桥接，请从 AstrBot 插件详情页打开。";
    return;
  }
  await bridge.ready();
  try {
    await load();
  } catch (error) {
    metaLine.textContent = `读取失败：${error.message}`;
    toast(`读取配置失败：${error.message}`, "bad");
    return;
  }
  document.getElementById("saveBtn").addEventListener("click", save);
  document.getElementById("testBtn").addEventListener("click", runTest);
  document.getElementById("calibrateBtn").addEventListener("click", runCalibration);
  document.getElementById("testQuestion").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runTest();
  });
}

main();
