# AstrBot 插件完整可执行方案：Jev 海龟汤

**插件名**：`astrbot_plugin_situation_puzzle`　**展示名**：Jev 海龟汤　**最低 AstrBot**：`>=4.16,<5`

> 目标：把 Situation-Puzzle 的「AI 主持海龟汤」玩法搬进 IM（QQ / Telegram / 企业微信 / Discord…），
> 并提供 AstrBot WebUI 内的完整前端设置面板（原生配置页 + 自定义可视化视图）。
> 本方案四个关键决策已锁定（§0），可直接按 §13 的实施计划开工。

---

## 0. 已锁定的决策（2026-10-19 讨论结论）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 判定引擎 | **默认 `jev`**（原版 SystemOne 协议），可切大模型 `provider` 模式；jev 引擎支持在面板里配置自定义 `base_url / api_key / model` |
| 2 | 题库 | **内置上游 `main` 分支全量 350 题**（`resources/library.seed.json` 即 `data/library.json` 快照），装完即全量；面板保留导入/导出/在线同步（`dev` 分支的 446 题实验批次出处不全，不随包发布） |
| 3 | 群聊触发 | **三通道同时生效**：@bot、引用/回复 bot 消息、`海龟 `关键词前缀（前缀可配） |
| 4 | 运行状态页 | **完整做**，含 SSE 实时日志 |

我拍板的默认值（不满意随时推翻，改动都在面板层）：

- 「揭晓」所有人可揭，但开局后锁 30 秒（`reveal_lock_s`），防开局秒泄底
- 计分极简：只记破解数 / 接近数 / 提问数，`/汤 排行` 纯文本，不做图
- M1~M3 只做中文；英文提示词已现成，英文面板 i18n 进 M4
- IM 端出题投稿本期不做
- 「判词一致性自检」（正反两问交叉验证，成本 ×2）作为可选配置进 M4，默认关

---

## 1. 移植范围：原项目哪些东西是"灵魂"

Situation-Puzzle 是 monorepo（apps/api + apps/web + packages/jev + PostgreSQL），直接搬搬不动。
真正决定游戏体验的是 `packages/jev` 的判题语义，1:1 复刻：

| 机制 | 原始实现（packages/jev/src） | 插件里的处理 |
|---|---|---|
| 提问判定 | 三选项 `是 / 否 / 无关`，另有 `uncertain=无法确定` | 同 |
| 还原判定 | 三选项 `破解成功 / 接近真相 / 还没猜对` | 同 |
| 置信度阈值 | `threshold=0.5`，`confidence < threshold` 一律降级为 `uncertain` | 同，可配 |
| 协议严格性 | **缺 probabilities 视为协议错误，禁止默认 0** | 同，解析失败不编造结果 |
| 重试策略 | 单次 20s 超时、最多重试 1 次、总期限 45s；429 尊重 `Retry-After`；401/403 立即失败 | 同 |
| 上下文 | 最近 N 轮已判定问答（旧在前）拼进 state，代词消解 | 同，默认 5 轮 |
| 题目元信息 | `category(本格/变格)`、`coreFacts` 写进判题上下文 | 同 |
| 提示分层 | 每题 3 条，逐条解锁 | 同 |
| 还原宽容度 | 允许同义表达、不同叙述顺序、省略非核心细节 | 同（提示词原文） |
| 防剧透 | 汤底只进 system prompt，绝不进对话输出 | 同，加单测断言 |

> 结论：**移植的是判题协议 + 提示词 + 题库，不是 Web 应用**。

---

## 2. IM 适配的关键差异与对策

| Web 端 | IM 里 | 插件对策 |
|---|---|---|
| 点「提问」按钮 | 消息即提问 | **三通道**：@bot / 回复 bot / `海龟 ` 前缀；优先级 `指令 > 回复bot > @bot > 关键词` |
| 8 人房间 + 邀请链接 | 群聊天然就是房间 | **一个群 = 一局**（`room_scope=group`），或每人独立进度（`room_scope=user`，群内 @ 回复） |
| 每房 8 条排队、每人 1 条 | 群里刷屏 | 房间级 `asyncio.Queue(maxsize=8)` + 单 worker，超出回「主持人正在思考，排队中」 |
| 实时同步 | IM 广播天然同步 | 无需 WS，回复即广播 |
| 90 秒离线转让房主 | 无"房主"概念 | `idle_timeout` 自动归档（默认 30 分钟），过期 `/汤 状态` 提示重新开局 |
| 汤底可能很长 | 平台单条长度限制 | 模板层分段（按 `\n\n` / 句号 / 硬切，`max_chars` 可配，段间 0.3s） |
| 匿名令牌 / 登录 | 平台账号 | `unified_msg_origin + sender_id` 作身份，无需登录 |
| 按钮选难度 | 文本指令 | `/汤 开 困难`、`/汤 开 #42`（题号） |

---

## 3. 整体架构

```
IM 平台(QQ/TG/企微/Discord)
        │  消息事件
        ▼
AstrBot 事件总线 ──► 插件 Router（三通道过滤/限流/房间匹配）
                          │
                          ▼
                   RoomManager（每 umo 一个房间，串行队列）
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
        JudgeEngine              PuzzleStore
   ┌────────┴────────┐         (SQLite + JSON)
   ▼                 ▼                │
Jev SystemOne    AstrBot Provider     │
(默认, 自定义api)  (可选, 大模型)      │
              │                       │
              └───────────┬───────────┘
                          ▼
                  TemplateEngine（防剧透/分段/变量）
                          │
                          ▼
                     IM 回复

WebUI ◄── Web API（context.register_web_api）
  ├─ 原生设置页（_conf_schema.json，自动渲染）
  └─ 可视化视图 views/{settings,library,dashboard}/index.html
```

---

## 4. 插件目录结构

```
astrbot_plugin_situation_puzzle/
├── main.py                      # Star 入口：指令组、三通道消息路由、Web API 注册
├── metadata.yaml                # 元数据（display_name / short_desc / support_platforms / astrbot_version）
├── _conf_schema.json            # 原生设置面板（§7.1）
├── requirements.txt             # httpx>=0.27（AstrBot 自带，写明范围）
├── logo.png                     # 256x256
├── README.md                    # 玩法、配置、来源署名、许可
├── LICENSE                      # GPL-3.0（见 §12）
│
├── core/
│   ├── __init__.py
│   ├── config.py                # 配置读写、默认值补齐、热更新
│   ├── store.py                 # SQLite（sqlite3 + asyncio.to_thread）+ 题库 JSON
│   ├── models.py                # Puzzle / Room / Verdict 数据类
│   ├── room.py                  # 房间状态机、队列、参与者、闲置回收
│   ├── router.py                # 消息→（指令 | 提问 | 忽略）三通道判定
│   ├── templates.py             # 消息模板（变量替换、分段、防剧透断言）
│   └── stats.py                 # 计数聚合、排行榜
│
├── judge/
│   ├── __init__.py
│   ├── base.py                  # JudgeEngine 抽象：ask() / solve() / review()
│   ├── protocol.py              # 选项枚举映射、置信度校验、错误分类
│   ├── prompts.py               # 中英提示词（移植自 packages/jev/src/prompts.ts）
│   ├── jev_engine.py            # SystemOne HTTP 协议（默认引擎，自定义 api/key/endpoint）
│   ├── provider_engine.py       # AstrBot LLM（llm_generate，可选大模型模式）
│   └── retry.py                 # 超时/重试/退避/Retry-After
│
├── web/
│   ├── api.py                   # 全部 Web API handler
│   └── schemas.py               # 输入校验
│
├── views/                       # AstrBot 可视化视图（iframe + bridge）
│   ├── settings/
│   │   ├── index.html
│   │   ├── app.js
│   │   └── style.css
│   ├── library/
│   │   ├── index.html
│   │   └── app.js
│   └── dashboard/
│       ├── index.html
│       └── app.js
│
├── resources/
│   └── library.seed.json        # 内置题库 350 题（data/library.json 快照，见 §11）
│
├── .astrbot-plugin/i18n/
│   ├── zh-CN.json               # views 标题/描述 + 面板文案
│   └── en-US.json               # M4
│
└── tests/
    ├── test_protocol.py
    ├── test_prompts.py
    ├── test_room.py
    ├── test_router.py           # 三通道触发判定
    └── test_noleak.py           # 断言汤底不出现在任何非揭晓消息里
```

持久化数据落在 `data/plugin_data/astrbot_plugin_situation_puzzle/`（**不放插件目录**，避免更新丢数据）。

---

## 5. 指令设计

指令组 `/汤`，别名 `/海龟汤`、`/turtle`、`/sp`。

| 指令 | 说明 | 权限 |
|---|---|---|
| `/汤` | 随机开局；`/汤 开`、`/汤 开 困难`、`/汤 开 #42` | 所有人 |
| `/汤 答 <推理>` | 提交还原，返回 破解成功 / 接近真相 / 还没猜对 | 所有人 |
| `/汤 提示` | 解锁下一条提示（1/3 → 2/3 → 3/3） | 所有人 |
| `/汤 揭晓` | 公布汤底并结束（该局不计分；开局 30s 内不可揭） | 所有人（可配仅管理员） |
| `/汤 跳过` | 放弃当前题，可立即换一题 | 所有人 |
| `/汤 状态` | 当前题号、已问轮数、剩余提示、已参与者 | 所有人 |
| `/汤 排行` | 本群解谜榜（破解数 / 接近数 / 提问数） | 所有人 |
| `/汤 题库` | 题库统计、按难度分布、随机题号列表 | 管理员 |
| `/汤 帮助` | 规则与指令速查 | 所有人 |

### 局内提问：三通道同时生效

- **@bot**：`@机器人 她是自杀吗？`
- **回复触发**：引用/回复 bot 的任意消息视为提问
- **关键词前缀**：`海龟 她是自杀吗？`（前缀可配 `keyword_prefix`，默认 `海龟`，必须带空格，空格后为正文）

### 路由判定优先级（`core/router.py`）

```
1. 指令命中（/汤 …、/海龟汤 …、别名）→ 指令处理器
2. 引用/回复 bot 消息 且 有活跃房间 → 提问
3. @bot 且 有活跃房间 → 提问（剥掉 at 段后的纯文本）
4. 消息以 "{keyword_prefix} " 开头 → 有活跃房间：提问；无房间：自动开局并把余文作为第一个问题
5. 其余消息 → 不响应（event 不拦截，放行给其它插件）
```

无活跃房间时三通道都不响应（`海龟 ` 前缀除外，它自动开局）——**闲聊零误伤**。

判定结果渲染（可模板化）：

```
「她是自杀吗？」→ 否
```
置信度不足 → `这个问题我得再看看（无法确定）`，**不带数字**，避免玩家据此反推。

---

## 6. 判定引擎

### 6.1 `jev` 模式（**默认**，原版 SystemOne 协议，自定义 api/key）

完全复刻 `packages/jev/src/client.ts`：

```
POST {base_url}
Authorization: Bearer {api_key}
Accept-Encoding: identity
{
  "model": "jev-1.13.0",
  "state": { "汤面": "...", "真相": "...", "类别": "本格：…", "核心事实": [...],
             "历史问答": ["「…」→ 是"], "玩家提问": "…" },
  "questions": {
    "ask": { "type": "choice", "instructions": "根据真相判断玩家提问，只能选择是、否、无关。",
             "criteria": { "是": "…", "否": "…", "无关": "…" } }
  }
}
```

- `base_url / api_key / model` 全部可在面板自定义（默认 `base_url=https://api.typesafe.ai/v1/systemone`、`model=jev-1.13.0`）
- 响应解析 `answers.ask.{choice, probabilities}`，`probabilities[choice]` 即 confidence
- **缺 probabilities → `protocol` 错误，禁止默认 0**
- `429` → 读 `Retry-After`；`401/403` → `auth` 立即失败并在回复里提示去面板检查 Key
- `state` 键名与原项目逐字段一致（zh/en 各一套），换语言不改变判题语义

### 6.2 `provider` 模式（可选，走 AstrBot 已配大模型）

- 判题模型在面板单独指定（`_special: select_provider`），空 = 跟随当前会话模型
- 调用：`provider_id = await self.context.get_current_chat_provider_id(umo=umo)`，
  `await self.context.llm_generate(chat_provider_id=..., prompt=..., system_prompt=...)`
- 输出契约：`{"choice": "是", "confidence": 0.87}`（`reason` 可选，**只落日志，不发玩家**）
- 解析：剥 ```json 围栏 → 取最后一个平衡的 `{...}` → `json.loads` → 校验 choice 在枚举内、confidence 是 0~1 浮点
- 失败即 `protocol` 错误：**重试 1 次（附"只输出 JSON"强化）→ 仍失败回「主持人走神了，再问一次」，绝不猜测**
- 可选 `self_consistency=3`：同问采样 3 次多数投票，置信度取赞成票均值（成本 ×3，默认关）

> 注意：普通聊天模型自报的 confidence 未校准，**不等价于 SystemOne 概率分布**。
> 兜底：阈值面板可调（0.4~0.8 滑块 + 说明）+ 默认开启"低置信度 → 无法确定"。

### 6.3 自动降级（M4）

`provider` 判出 `uncertain` 且配置了 jev 引擎 → 用 jev 复判一次（对应原项目 MiniMax 补判）。两次都不确定 → 回「无法确定」。
可选「判词一致性自检」：同问题正反两问交叉验证，矛盾则强制 uncertain（成本 ×2，默认关）。

### 6.4 并发与限流

- 每个房间 `asyncio.Queue(maxsize=8)` + 单 worker，保证同房间提问严格串行
- 全局 `asyncio.Semaphore(4)` 限制同时判题数
- 用户冷却 3s、每日上限（0=不限）
- 所有超时基于 `total_deadline=45s`，绝不无限挂起

---

## 7. 前端面板

### 7.1 第一层：`_conf_schema.json` 原生设置页

AstrBot 把 schema 自动渲染成表单，分 5 组（`object` 嵌套即分组）：

```json
{
  "enabled": { "type": "bool", "description": "启用插件", "default": true },
  "basic": {
    "type": "object",
    "description": "基础设置",
    "items": {
      "host_name": { "type": "string", "description": "主持人名字", "default": "Jev" },
      "language": { "type": "string", "description": "判定与文案语言", "default": "zh", "options": ["zh", "en"] },
      "room_scope": { "type": "string", "description": "开房范围", "default": "group", "options": ["group", "user"] },
      "keyword_prefix": { "type": "string", "description": "关键词触发前缀", "default": "海龟", "hint": "发送「海龟 问题」即可提问，需带空格" },
      "default_difficulty": { "type": "string", "description": "默认难度", "default": "random", "options": ["random", "easy", "medium", "hard"] },
      "allow_group": { "type": "bool", "description": "允许群聊开局", "default": true },
      "allow_private": { "type": "bool", "description": "允许私聊开局", "default": true }
    }
  },
  "judge": {
    "type": "object",
    "description": "判题引擎",
    "items": {
      "engine": { "type": "string", "description": "引擎", "default": "jev", "options": ["jev", "provider"] },
      "jev_api_key": { "type": "string", "description": "Jev API Key", "default": "", "secret": true },
      "jev_base_url": { "type": "string", "description": "Jev 接口地址", "default": "https://api.typesafe.ai/v1/systemone" },
      "jev_model": { "type": "string", "description": "Jev 模型", "default": "jev-1.13.0" },
      "provider_id": { "type": "string", "description": "provider 模式的判题模型", "default": "", "_special": "select_provider", "hint": "留空则跟随当前会话模型" },
      "threshold": { "type": "float", "description": "置信度阈值", "default": 0.5, "hint": "低于阈值的判定降级为「无法确定」" },
      "attempt_timeout_ms": { "type": "int", "description": "单次超时(ms)", "default": 20000 },
      "total_deadline_ms": { "type": "int", "description": "总期限(ms)", "default": 45000 },
      "max_retries": { "type": "int", "description": "失败重试次数", "default": 1 },
      "self_consistency": { "type": "int", "description": "采样次数", "default": 1, "hint": ">1 为多数投票，成本成倍" },
      "recent_qa_window": { "type": "int", "description": "历史问答窗口", "default": 5 }
    }
  },
  "game": {
    "type": "object",
    "description": "玩法",
    "items": {
      "hint_max": { "type": "int", "description": "提示上限", "default": 3 },
      "idle_timeout_min": { "type": "int", "description": "闲置自动结束(分钟)", "default": 30 },
      "reveal_policy": { "type": "string", "description": "揭晓权限", "default": "all", "options": ["all", "admin"] },
      "reveal_lock_s": { "type": "int", "description": "开局揭晓锁定(秒)", "default": 30 },
      "allow_replay": { "type": "bool", "description": "允许重玩已解题", "default": false }
    }
  },
  "limit": {
    "type": "object",
    "description": "限流",
    "items": {
      "user_cooldown_s": { "type": "int", "description": "用户提问冷却(秒)", "default": 3 },
      "room_queue_limit": { "type": "int", "description": "房间排队上限", "default": 8 },
      "daily_limit": { "type": "int", "description": "每日对局上限", "default": 0, "hint": "0 表示不限" }
    }
  },
  "message": {
    "type": "object",
    "description": "消息模板",
    "items": {
      "max_chars": { "type": "int", "description": "单条字数上限", "default": 1500 },
      "tpl_start": { "type": "text", "description": "开局模板", "default": "【{title}】\n{story}\n\n我是{host}，这碗汤的主持人。@我提问，我只回答 是 / 否 / 无关。\n/汤 提示 · /汤 答 <推理> · /汤 揭晓" },
      "tpl_yes": { "type": "text", "description": "判定模板", "default": "「{question}」→ {verdict}" },
      "tpl_uncertain": { "type": "text", "description": "不确定模板", "default": "这个问题我得再看看（无法确定）" },
      "tpl_solved": { "type": "text", "description": "破解模板", "default": "破解成功！\n\n{answer}" },
      "tpl_close": { "type": "text", "description": "接近模板", "default": "接近真相了，但还缺一环。" }
    }
  }
}
```

模板变量：`{title} {story} {answer} {hints} {host} {question} {verdict} {rounds} {solved_by} {score}`。

### 7.2 第二层：`views/` 可视化视图

> 官方机制：`views/<name>/index.html` + `window.AstrBotPluginView` bridge（自动注入 `bridge-sdk.js`）
> + `context.register_web_api()`。iframe 沙箱 `allow-scripts allow-forms allow-downloads`，
> 主题走 `<html data-theme>`，i18n 走 `.astrbot-plugin/i18n/<locale>.json` 的 `views.<name>`。

#### ① `views/settings` — 高级设置

- **引擎选择卡片**：jev / provider 切换，联动显示 Key、Endpoint、模型字段
- **连通性自检**：内置测试题（"终点线"）+ 测试问题，点「试判」→ `判定 / 置信度 / 耗时 / 引擎 / 原始响应(折叠)`
- **阈值校准**：滑块 0.4~0.8，旁边用真实题目跑一组（是/否/无关各 3 问）给出命中率
- **消息模板编辑器**：textarea + 变量 chips + 右侧「玩家视角预览」
- **保存**：`bridge.apiPost("settings/save", payload)` → 写回 `AstrBotConfig` 并 `save_config()`，**无需重启**

#### ② `views/library` — 题库管理

- 表格：搜索（标题/汤面/汤底）、难度/分类筛选、分页、启用开关（350 题内置，默认全启用）
- 详情抽屉：汤面 / 汤底 / 3 条提示 / 出处链接 / 编辑 / 删除
- **导入**：拖拽 `library.json` 或单题 JSON；冲突策略「按 id 覆盖 / 跳过 / 新建 id」
- **导出**：JSON / CSV
- **同步官方库**：一键从 GitHub raw 拉 `data/library.json` 合并
- **题目体检**：批量检查「汤面为空 / 提示不足 3 条 / 汤底太短 / 重复」，可选 LLM review（原项目投稿审核提示词）

#### ③ `views/dashboard` — 运行状态（完整做）

- 活跃房间表（umo、题号、回合数、剩余提示、最后活跃）
- 今日指标：开局数、破解率、平均回合、平均判题耗时、P95
- 判题调用分布：引擎 / 选项占比 / 错误分类（network / upstream / rate_limited / auth / protocol / timeout）
- **SSE 实时日志**（`bridge.subscribeSSE("events")`）：判题流水滚动显示

### 7.3 Web API 路由表

后端路由必须带插件名前缀；前端 bridge 的 endpoint 不带前缀。

| 方法 | 路由 | 用途 |
|---|---|---|
| GET | `/{p}/settings` | 读配置 + 引擎元信息（可用 provider 列表、题库统计） |
| POST | `/{p}/settings/save` | 校验并保存，返回生效后的快照 |
| POST | `/{p}/engine/test` | 单次试判，返回 verdict/confidence/latency/raw |
| POST | `/{p}/engine/calibrate` | 一组样本的命中率统计 |
| GET | `/{p}/library/list` | 分页 + 搜索 + 筛选 |
| POST | `/{p}/library/upsert` | 新增/编辑 |
| DELETE | `/{p}/library/<id>` | 删除（软删） |
| POST | `/{p}/library/import` | 上传 JSON（`request.files()`，字段名 `file`） |
| GET | `/{p}/library/export` | 导出文件（`file_response`） |
| POST | `/{p}/library/sync` | 从远端拉取并合并 |
| POST | `/{p}/library/lint` | 体检 |
| GET | `/{p}/stats` | 统计聚合 |
| GET | `/{p}/rooms` | 活跃房间 |
| GET | `/{p}/events` | SSE 日志流（`stream_response`） |

所有 handler 用 `astrbot.api.web` 的 `request / json_response / error_response`，不混用 Quart。
输入全部自校验（id 白名单、长度上限、分页上限 200），文件只落 `data/plugin_data/<plugin>/imports/` 且重命名。

### 7.4 降级策略

若运行环境较老（无 `register_web_api` / 不识别 `views/`）：
`try: context.register_web_api(...) except AttributeError: logger.warning(...)`，插件本体照常工作，只保留 `_conf_schema` 面板。
`metadata.yaml` 写 `astrbot_version: ">=4.16,<5"`。

---

## 8. 数据模型

SQLite（`sqlite3` + `asyncio.to_thread`，不引第三方 ORM）：

```sql
puzzles(id TEXT PK, title, category, difficulty, story, answer,
        hints_json, core_facts_json, source, source_url,
        enabled INT DEFAULT 1, play_count INT DEFAULT 0,
        solved_count INT DEFAULT 0, created_at, updated_at)

rooms(umo TEXT, room_id TEXT, puzzle_id TEXT, state TEXT,      -- playing/solved/revealed/skipped
      started_at, updated_at, hints_used INT, rounds INT,
      participants_json, owner_id TEXT, PRIMARY KEY(umo, room_id))

rounds(id INTEGER PK AUTOINCREMENT, umo, puzzle_id, user_id,
       question TEXT, kind TEXT,          -- ask/solve
       verdict TEXT, confidence REAL, engine TEXT,
       latency_ms INT, error_class TEXT, created_at)

scores(umo, user_id, solved INT, close INT, asked INT, updated_at,
       PRIMARY KEY(umo, user_id))

kv(key TEXT PK, value TEXT)               -- 统计聚合、seed 版本
```

索引：`rounds(umo, created_at)`、`puzzles(difficulty, enabled)`。

---

## 9. 核心流程伪代码

### 9.1 消息路由（`core/router.py`）

```python
async def route(self, event) -> RouteResult:
    room = self.rooms.get(event.unified_msg_origin)
    text = event.message_str.strip()

    if text.startswith(("/汤", "/海龟汤", "/turtle", "/sp")):
        return Cmd(text)                                   # 1. 指令
    if self.is_reply_to_bot(event) and room and room.active:
        return Question(room, text)                        # 2. 回复触发
    if self.has_at_bot(event) and room and room.active:
        return Question(room, self.strip_at(event))        # 3. @触发
    if text.startswith(self.keyword_prefix + " "):
        q = text[len(self.keyword_prefix) + 1:].strip()
        if room and room.active:
            return Question(room, q)                       # 4a. 局内关键词
        return StartThenQuestion(q)                        # 4b. 无房时自动开局
    return Ignore()                                        # 5. 放行
```

### 9.2 提问处理（`core/room.py`）

```python
async def handle_question(self, room, user_id, question):
    if self.cooldown.hit(user_id): return Reply("慢点，让我喘口气…")
    if room.queue.full():          return Reply("主持人正在思考，排队中（最多 8 条）")
    await room.queue.put((user_id, question))              # 单 worker 串行消费

async def _worker(self, room):
    while True:
        user_id, question = await room.queue.get()
        async with GLOBAL_SEM:                             # 全局并发 4
            recent = await self.store.recent_qas(room, n=self.cfg.recent_qa_window)
            result = await self.engine.ask(
                AskInput(title=room.puzzle.title, surface=room.puzzle.story,
                         answer=room.puzzle.answer, core_facts=room.puzzle.core_facts,
                         category=room.puzzle.category, recent_qas=recent, question=question))
        await self.store.record_round(room, user_id, question, result)
        await room.reply(tpl(self.cfg.tpl_verdict, question=question,
                             verdict=LABEL[result.verdict]))   # verdict ∈ {是,否,无关,无法确定}
```

### 9.3 判题重试（`judge/retry.py`，对应原项目 client.ts）

```python
deadline = now() + cfg.total_deadline_ms
for attempt in 1..(1 + cfg.max_retries):
    remaining = deadline - now()
    if remaining <= 500: break
    try:
        return await self._request_once(timeout=min(cfg.attempt_timeout_ms, remaining))
    except JevError as e:
        if e.cls == "auth" or e.cls == "protocol": raise          # 立即失败
        if e.cls == "rate_limited" and e.retry_after_ms:
            await sleep(min(e.retry_after_ms, deadline - now()))
        else:
            await sleep(300 * attempt)
raise last_error                                                  # 调用方回友好话术
```

---

## 10. 题库

- `resources/library.seed.json` = 原项目 `main` 分支 `data/library.json` **350 题快照**
  （字段 `id/title/category/story/answer/hints[]/difficulty`，零映射直接入库）
- 首次启动把 seed 写入 SQLite（`kv.seed_version` 防重复导入）；面板可导入/导出/同步官方库
- 每题保留 `source / source_url`（B 站 / 抖音创作者逐题署名）

---

## 11. 合规

- 原项目 **GPL-3.0**。移植 `prompts.ts` / `client.ts` 逻辑与文案 → 插件以 **GPL-3.0** 发布，保留 `LICENSE` 与版权声明。
- README 显著位置：灵感来源、原项目链接、移植范围、题库创作者致谢。
- 题库内容版权归原作者，不随代码"重新授权"，仅在 `source` 字段标注来源。
- AstrBot 社区规范：`requirements.txt` 齐备、用 `httpx`/`aiohttp` 而非 `requests`、`ruff` 格式化、错误不外抛、持久化进 `data` 目录。

---

## 12. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| provider 模式 confidence 不准 | 该"无法确定"时硬答 | 默认引擎是 jev（真概率）；provider 模式阈值可调 + 试判/校准面板 + 可选投票/一致性自检 |
| 三通道触发误判 | 误回复闲聊 | 指令/回复/@/带空格关键词前缀四种显式信号，零启发式；测试 `test_router.py` 覆盖 |
| 长汤底触发平台分段 | 消息被截断 | 模板层分段 + `max_chars` 配置 + 段间 0.3s |
| LLM 输出非 JSON | 判定失败 | 三级解析 + 重试 1 次 + 明确失败话术，绝不 fallback 到"是" |
| 题库版权 / 许可 | 无法上架 | GPL-3.0 + 逐题署名 + README 声明 |
| AstrBot 版本差异 | 面板不显示 | `astrbot_version` 声明 + 能力检测降级 |
| 群聊开局 30s 内被恶意揭晓 | 泄底 | `reveal_lock_s=30` + `reveal_policy` 可配 |

---

## 13. 实施计划

### M0 · 环境准备（半小时）

1. 本机跑起 AstrBot：`git clone https://github.com/AstrBotDevs/AstrBot`，`pip install -r requirements.txt`，`python main.py`
2. WebUI（默认 6185）配一个 LLM provider；确认内置 webchat 能对话
3. 插件目录放 `AstrBot/data/plugins/astrbot_plugin_situation_puzzle/`（开发期 symlink 到本仓库）
4. 确认 WebUI 插件页能看到本插件卡片

### M1 · 最小可玩闭环

| 任务 | 文件 | 验收 |
|---|---|---|
| 骨架 + 元数据 | `metadata.yaml` `_conf_schema.json` `logo.png` | WebUI 能加载插件、设置页能渲染 |
| 题库层 | `core/store.py` `core/models.py` `resources/library.seed.json` | 启动后 `SELECT COUNT(*) FROM puzzles` = 350 |
| 判题引擎 | `judge/protocol.py` `judge/prompts.py` `judge/jev_engine.py` `judge/provider_engine.py` `judge/retry.py` | 单测 mock HTTP 通过；真实 jev 接口手动试判返回合法 verdict |
| 房间与路由 | `core/room.py` `core/router.py` `core/templates.py` | 单测覆盖三通道 + 不吃闲聊 + 防剧透断言 |
| 指令组 | `main.py` | webchat 里 `/汤` → 开局 → `海龟 是自杀吗` → 判定 → `/汤 提示` → `/汤 答 …` → `/汤 揭晓` 全流程跑通 |

**M1 出口**：webchat 完整玩一局，断网/Key 错误时有可读提示。

### M2 · 可视化面板

| 任务 | 文件 | 验收 |
|---|---|---|
| Web API | `web/api.py` `web/schemas.py` | bridge 调通 `settings/save`、`engine/test`、`library/list` |
| 设置页 | `views/settings/` | 切引擎联动字段、试判显示置信度/耗时、保存即时生效 |
| 题库页 | `views/library/` | 搜索/分页/增删/导入导出/同步官方库可用 |
| i18n + 主题 | `.astrbot-plugin/i18n/zh-CN.json`、CSS 变量 | 亮暗主题切换不破版 |

**M2 出口**：不改代码，全程面板完成「换 Key → 试判 → 导入题目 → 开局游玩」。

### M3 · 稳定性与数据

| 任务 | 内容 | 验收 |
|---|---|---|
| 并发 | 房间队列 + 全局信号量 + 冷却/上限 | 同房间 8 条并发提问严格串行、跨房间互不阻塞 |
| 落库 | rounds/scores/kv、闲置归档 | 重启后对局记录与排行榜不丢 |
| 容错 | 断网/429/401/超时四场景 | 各场景回复可读，不刷屏、不崩溃 |
| 测试 | `tests/` 五个文件 | `pytest` 全绿，`ruff check` 通过 |

### M4 · 打磨与发布

| 任务 | 内容 |
|---|---|
| `views/dashboard` | 统计聚合 + SSE 实时判题流水（`subscribeSSE("events")`） |
| 双引擎降级 | provider uncertain → jev 复判；可选正反一致性自检 |
| 英文 | `en-US.json`、英文提示词开关 |
| 发布 | README（玩法截图、配置说明、致谢）、LICENSE、GitHub 仓库、插件市场上架提交 |

### 总体验收清单

- [ ] 判定语义与官方一致：固定题 + 20 条标准问题，人工抽检一致率 ≥ 90%
- [ ] 低置信度必回「无法确定」，**任何情况下不猜测**
- [ ] 汤底零泄露：单测遍历所有模板断言不含 `answer`
- [ ] 三通道触发互不串扰，无房间时闲聊零响应
- [ ] 同房间提问串行、跨房间并行；队列满有友好提示
- [ ] 面板保存 → 无需重启生效；重启后配置与题库不丢
- [ ] 断网 / 429 / 401 / 超时 四种故障都有可读回复
- [ ] 开局 30s 内不可揭晓
- [ ] `pytest` 全绿、`ruff check` 通过、README 可照做安装

---

## 14. 开发调试方法（无真实 IM 也能测）

1. **webchat**：AstrBot 内置网页聊天窗口当 IM 用，`@bot` 用 `@` 手打、回复触发用引用按钮模拟不了——改用手动构造消息链单测覆盖
2. **插件热重载**：WebUI 插件页点「重载插件」，改代码即生效
3. **判题单测**：mock `httpx` 响应跑 `jev_engine`；`provider_engine` 用假 `context.llm_generate` 桩
4. **views 调试**：WebUI 插件详情页打开视图，浏览器 DevTools 看 bridge 调用与 SSE
5. **真实联调**：M3 后接 aiocqhttp（NapCat）或 Telegram 实测群聊

---

## 15. 实施状态（2026-10-19 完成）

方案中的 M1~M4 已落地到 `astrbot_plugin_situation_puzzle/`：

| 模块 | 状态 | 说明 |
|---|---|---|
| 插件骨架 | 完成 | `metadata.yaml`、`_conf_schema.json`、`logo.png`、`README.md`、GPL-3.0 |
| 题库 | 完成 | 350 题内置（对齐上游 main 分支），首次启动幂等入库（`kv.seed_version`） |
| 判题引擎 | 完成 | `jev`（默认，自定义 base_url/api_key/model）+ `provider`（可选，支持采样投票）+ 统一重试与错误分类 |
| 房间与路由 | 完成 | 每会话一局、串行锁、排队上限、冷却、日限、闲置回收、三通道触发 |
| 指令 | 完成 | 开局/答/提示/揭晓/跳过/状态/排行/题库/帮助；主指令与子指令均可换词 |
| 内容安全 | 完成 | 模板不泄露汤底；`tests/test_noleak.py` 断言；开局 30 秒锁揭晓 |
| 面板后端 | 完成 | 15 个 Web API（配置、试判、校准、题库增删改查/导入导出/同步/体检、统计、房间、日志、SSE） |
| 面板前端 | 完成 | `views/settings`、`views/library`、`views/dashboard`，亮暗主题 + 中英文案 |
| 测试 | 完成 | 81 个用例（协议/提示词/路由/指令映射/房间/存储/防剧透/schema 一致性/端到端/面板接口） |
| 质量 | 完成 | `ruff check` 与 `ruff format --check` 全绿 |

验证结果：

```
pytest tests -q          → 81 passed
ruff check .             → All checks passed
ruff format --check .    → 32 files already formatted
node --check views/*/app.js → 三个视图脚本语法通过
```

2026-10-19 追加：

- `basic.command_style`：`both` / `slash` / `text`，指令写法可选斜杠或纯文字。
- `basic.command_names` + `basic.sub_commands`：指令映射。主指令与 9 个子指令都能换词，
  设置页可视化编辑（逗号分隔、实时预览、恢复默认）；帮助与开局文案按当前映射生成；
  跨组重名、非法字符、空主指令在保存时整批拒绝。

尚未完成，需要真实环境：

- 真实 IM 联调：`Reply` 组件在部分适配器下没有 `sender_id`，`_flags()` 已做兜底（未知发送者时按「回复 bot」处理），需在 aiocqhttp / Telegram 实测确认
- jev 真实 Key 下的判题质量回归：用 20 条标准问题人工抽检一致率是否 ≥ 90%
- 插件市场上架与官网文档页

预览方式（无 AstrBot 时）：`dev/preview/` 是桩件预览，`python -m http.server 5173` 后访问
`http://127.0.0.1:5173/dev/preview/index.html?view=settings|library|dashboard`。
