const bridge = window.AstrBotPluginView;
const PAGE_SIZE = 20;

const state = { page: 1, total: 0, items: [], editing: null };
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

function filters() {
  return {
    q: document.getElementById("q").value.trim(),
    difficulty: document.getElementById("difficulty").value,
    category: document.getElementById("category").value,
    enabled: document.getElementById("enabled").value,
  };
}

async function load(page = 1) {
  const params = { page, size: PAGE_SIZE, ...filters() };
  const data = await bridge.apiGet("library/list", params);
  state.page = data.page;
  state.total = data.total;
  state.items = data.items || [];
  render();
}

function render() {
  const wrap = document.getElementById("listWrap");
  wrap.innerHTML = "";
  document.getElementById("summary").textContent = `共 ${state.total} 题 · 第 ${state.page} 页`;

  if (!state.items.length) {
    wrap.appendChild(node("p", { class: "sub", text: "没有匹配的题目。" }));
    renderPager();
    return;
  }

  const table = node("table", { class: "list" });
  table.appendChild(node("tr", {},
    node("th", { text: "题目" }),
    node("th", { text: "类别" }),
    node("th", { text: "难度" }),
    node("th", { text: "提示" }),
    node("th", { text: "状态" }),
    node("th", { text: "数据" }),
    node("th", { text: "操作" }),
  ));

  for (const item of state.items) {
    const titleCell = node("td", { class: "title-cell" },
      node("strong", { text: item.title || item.id }),
      node("span", { class: "sub", text: `${item.id} · ${(item.story || "").slice(0, 42)}` }),
    );
    const toggle = node("button", {
      class: "link-btn",
      type: "button",
      text: item.enabled ? "停用" : "启用",
      onclick: () => toggleEnabled(item),
    });
    table.appendChild(node("tr", {},
      titleCell,
      node("td", { text: item.category || "—" }),
      node("td", { text: { easy: "简单", medium: "中等", hard: "困难" }[item.difficulty] || item.difficulty }),
      node("td", { text: `${(item.hints || []).length} 条` }),
      node("td", {}, node("span", { class: `pill ${item.enabled ? "on" : "off"}`, text: item.enabled ? "启用" : "停用" })),
      node("td", { class: "sub", text: `玩 ${item.play_count || 0} / 破 ${item.solved_count || 0}` }),
      node("td", {}, node("div", { class: "row-actions" },
        node("button", { class: "link-btn", type: "button", text: "编辑", onclick: () => openEditor(item) }),
        toggle,
      )),
    ));
  }

  wrap.appendChild(table);
  renderPager();
}

function renderPager() {
  const pager = document.getElementById("pager");
  pager.innerHTML = "";
  const pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  pager.appendChild(node("button", {
    class: "btn", type: "button", text: "上一页",
    onclick: () => state.page > 1 && load(state.page - 1),
  }));
  pager.appendChild(node("span", { text: `${state.page} / ${pages}` }));
  pager.appendChild(node("button", {
    class: "btn", type: "button", text: "下一页",
    onclick: () => state.page < pages && load(state.page + 1),
  }));
}

function openEditor(item) {
  state.editing = item || null;
  document.getElementById("modalTitle").textContent = item ? "编辑题目" : "新增题目";
  const fields = {
    "e-id": item?.id || "",
    "e-title": item?.title || "",
    "e-story": item?.story || "",
    "e-answer": item?.answer || "",
    "e-hints": (item?.hints || []).join("\n"),
    "e-source": item?.source || "",
    "e-source-url": item?.source_url || "",
  };
  for (const [id, value] of Object.entries(fields)) document.getElementById(id).value = value;
  document.getElementById("e-id").readOnly = Boolean(item);
  document.getElementById("e-category").value = item?.category || "本格";
  document.getElementById("e-difficulty").value = item?.difficulty || "medium";
  const enabled = document.getElementById("e-enabled");
  enabled.checked = item ? item.enabled !== false : true;
  document.getElementById("e-enabled-label").textContent = enabled.checked ? "开启" : "关闭";
  document.getElementById("modal").hidden = false;
}

function closeEditor() {
  document.getElementById("modal").hidden = true;
  state.editing = null;
}

async function savePuzzle() {
  const payload = {
    id: document.getElementById("e-id").value.trim(),
    title: document.getElementById("e-title").value.trim(),
    category: document.getElementById("e-category").value,
    difficulty: document.getElementById("e-difficulty").value,
    story: document.getElementById("e-story").value.trim(),
    answer: document.getElementById("e-answer").value.trim(),
    hints: document.getElementById("e-hints").value.split("\n").map((line) => line.trim()).filter(Boolean),
    source: document.getElementById("e-source").value.trim(),
    source_url: document.getElementById("e-source-url").value.trim(),
    enabled: document.getElementById("e-enabled").checked,
  };
  if (!payload.title || !payload.story || !payload.answer) {
    toast("标题、汤面、汤底都不能为空", "bad");
    return;
  }
  const button = document.getElementById("saveBtn");
  button.disabled = true;
  try {
    await bridge.apiPost("library/upsert", payload);
    closeEditor();
    toast("已保存");
    await load(state.page);
  } catch (error) {
    toast(`保存失败：${error.message}`, "bad");
  } finally {
    button.disabled = false;
  }
}

async function toggleEnabled(item) {
  try {
    await bridge.apiPost("library/upsert", { ...item, enabled: !item.enabled });
    toast(item.enabled ? "已停用，不再抽到该题" : "已启用");
    await load(state.page);
  } catch (error) {
    toast(`操作失败：${error.message}`, "bad");
  }
}

async function importFile(file) {
  if (!file) return;
  try {
    const result = await bridge.upload("library/import", file);
    toast(`导入完成：新增/更新 ${result.imported} 题，跳过 ${result.skipped} 题`);
    await load(1);
  } catch (error) {
    toast(`导入失败：${error.message}`, "bad");
  }
}

async function exportLibrary() {
  try {
    await bridge.download("library/export", { format: "json" }, "library.json");
  } catch (error) {
    toast(`导出失败：${error.message}`, "bad");
  }
}

async function syncLibrary() {
  if (!window.confirm("将从 GitHub 拉取官方题库并合并（同 id 覆盖）。继续？")) return;
  const button = document.getElementById("syncBtn");
  button.disabled = true;
  button.textContent = "同步中…";
  try {
    const result = await bridge.apiPost("library/sync", {});
    if (result.ok) toast(`同步完成：${result.imported} 题`);
    else toast(result.message || "同步失败", "bad");
    await load(1);
  } catch (error) {
    toast(`同步失败：${error.message}`, "bad");
  } finally {
    button.disabled = false;
    button.textContent = "同步官方库";
  }
}

async function lintLibrary() {
  const button = document.getElementById("lintBtn");
  button.disabled = true;
  try {
    const result = await bridge.apiPost("library/lint", {});
    const card = document.getElementById("lintCard");
    const list = document.getElementById("lintList");
    card.hidden = false;
    list.innerHTML = "";
    document.getElementById("lintSummary").textContent = `检查 ${result.total} 题，发现 ${result.count} 条提示`;
    if (!result.issues.length) {
      list.appendChild(node("p", { class: "sub", text: "没发现问题。" }));
    }
    for (const issue of result.issues) {
      list.appendChild(node("div", { class: `issue ${issue.level}` },
        node("span", { class: "lvl", text: issue.level }),
        node("span", { text: issue.id }),
        node("span", { class: "sub", text: issue.message }),
      ));
    }
  } catch (error) {
    toast(`体检失败：${error.message}`, "bad");
  } finally {
    button.disabled = false;
  }
}

async function main() {
  if (!bridge) {
    document.getElementById("summary").textContent = "未检测到插件页面桥接，请从 AstrBot 插件详情页打开。";
    return;
  }
  await bridge.ready();
  document.getElementById("searchBtn").addEventListener("click", () => load(1));
  document.getElementById("q").addEventListener("keydown", (event) => {
    if (event.key === "Enter") load(1);
  });
  document.getElementById("newBtn").addEventListener("click", () => openEditor(null));
  document.getElementById("cancelBtn").addEventListener("click", closeEditor);
  document.getElementById("saveBtn").addEventListener("click", savePuzzle);
  document.getElementById("importBtn").addEventListener("click", () => document.getElementById("fileInput").click());
  document.getElementById("fileInput").addEventListener("change", (event) => importFile(event.target.files[0]));
  document.getElementById("exportBtn").addEventListener("click", exportLibrary);
  document.getElementById("syncBtn").addEventListener("click", syncLibrary);
  document.getElementById("lintBtn").addEventListener("click", lintLibrary);
  document.getElementById("e-enabled").addEventListener("change", (event) => {
    document.getElementById("e-enabled-label").textContent = event.target.checked ? "开启" : "关闭";
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeEditor();
  });
  document.getElementById("modal").addEventListener("click", (event) => {
    if (event.target.id === "modal") closeEditor();
  });
  try {
    await load(1);
  } catch (error) {
    document.getElementById("summary").textContent = `读取失败：${error.message}`;
  }
}

main();
