# Changelog

## v0.3.0 — 2026-06-15

### 模型配置中心：Provider 从 .env 静态配置 → 运行时可视化管理

#### 新功能
- **模型配置面板**（右上角 ⚙）：随时新增 / 编辑 / 删除供应商，配置接口协议、Base URL、API Key、模型列表、默认模型、视觉开关，并一键设默认、连通测试（支持测试未保存的草稿）。
- **Provider 持久化存储**（`providers/store.py`）：配置落盘到 `data/providers.json`（git 忽略，含密钥不外发）。首次运行从既有 `.env` 播种四个供应商，沿用 `claude/anthropic_compat/deepseek/openai_compat` 作为稳定 id，历史会话的 step_models 引用不受影响。
- **动态 Provider 构建**（`providers/dynamic.py`）：按配置实时构建 Provider，复用既有 Anthropic Messages / OpenAI Chat Completions 两套调用逻辑。
- **项目重命名**：项目列表与工作台标题均可重命名；上传素材改名改为**原地编辑**（新增通用 `InlineEdit` 组件，回车/失焦保存、Esc 取消、双击进入），替代原 `prompt()` 弹框。

#### API
- `GET /api/providers`（含 `default_id`，列表不外泄 api_key）
- `GET /api/providers/{id}`（单条完整配置，供编辑回填）
- `POST /api/providers` 新增 · `PUT /api/providers/{id}` 编辑 · `DELETE /api/providers/{id}` 删除
- `PUT /api/providers/{id}/default` 设默认
- `POST /api/providers/test` 扩展为支持测试未保存的草稿配置（`config` 字段）

#### 健壮性修复
- **会话元数据并发安全**：`SessionStore` 加锁 + `_write_meta` 原子写（临时文件 + rename）+ 新增 `mutate()` 锁内读改写；改名、step-model 路由改走 `mutate`，杜绝多产出并行结束时互相覆盖 `meta.json`。
- **Runner 内存回收**：`(sid, step)` 任务完成即从内存表移除（历史已落盘，迟到订阅自动回退读盘），不再无限累积。

#### 清理与一致性
- 删除四个不再使用的具体 Provider 类（registry 重写后只复用 `_AnthropicBase` / `_OpenAICompatBase` 两个调用基类）。
- 移除 `StepDef` 中失效的 `default_provider/default_model` 字段（默认模型由配置面板的 store 默认项决定）。
- 生成失败文案由「检查 .env」改为「在右上角模型配置面板补全」。
- 删除误入的 `backend/package-lock.json` 与空目录 `backend/revision/`（版本管理实际在 `storage/`）。
- README / `.env.example` 说明 `.env` 仅用于首次播种，日常配置走面板。

#### UI 细节
- 模型配置面板：接口协议命名专业化（OpenAI Chat Completions / Anthropic Messages）；API Key 加眼睛图标明文/掩码切换；默认模型改为真正可点选的下拉；修复「支持视觉」复选框被全局 `input{width:100%}` 撑坏导致的换行排版问题。

## v0.2.0 — 2026-06-14

### 重大改版：五产出独立生成 + 横版 Tab + 矢量脉络图

#### 产出体系
- **五产出取代四产出**，文艺命名：实录（纠错逐字稿）、纲目（章节）、撷要（精炼版纪要）、笺注（详尽版纪要）、脉络（知识图谱）
- 每个产出独立生成、独立选模型（`PUT /api/sessions/{id}/step-model` 设置 provider/model）
- 依赖校验（`StepDef.requires` / `requires_any`）：未满足时前端 Tab 变灰、后端拒绝执行

#### 后台架构
- `pipeline/runner.py`：按 `(sid, step_key)` 维度的后台任务，事件落盘到 `progress/<step>.json`
- `POST /api/sessions/{id}/run-step?step=X`（幂等触发）+ `GET .../run-step-stream?step=X`（SSE 进度，含历史回放）
- `GET /api/sessions/{id}/progress`：一次性查所有步骤进度（刷新页面后恢复 UI 用）
- 老会话兼容：`LEGACY_NAMES` 映射老文件名（逐字稿/章节稿/纪要主体/知识图谱）→ 新名；`available_artifacts(sid)` 实时扫盘

#### 前端 UX
- **横版 Tab 布局**：5 个产出 Tab 横向排列，点击后内容在下方展开（替代原左 Tab/右面板结构）
- **全宽工作台**：`.content` `max-width` 去除，铺满浏览器（减去板块侧栏 248px）
- Tab 副标显示模型名，未设时回落到默认 provider 的 default_model（不再显示「未选模型」）

#### 脉络图（Mermaid）矢量缩放
- **关键修复**：`htmlLabels: false` → 节点文字走原生 SVG `<text>`，避免 `<foreignObject>` HTML 在 `transform: scale` 下被光栅化模糊
- 滚轮缩放以鼠标位置为锚点、＋／－ 以画布中心缩放、上限 600%
- 滚轮事件改用 `addEventListener("wheel", fn, {passive: false})` + `overscroll-behavior: contain` + `touch-action: none`，画布内滚轮不再带动页面滚动
- 横版 flowchart LR + A4 长宽比画布 + 暖米白底珊瑚渐晕

#### 输入素材区
- 自定义上传按钮（虚线珊瑚边 + ＋图标）替代原生 file input 样式
- 文件支持删除、重命名（`DELETE/PUT /api/sessions/{id}/inputs/{filename}`）
- 列表展示每个文件，hover 显示改名/删除按钮

#### 模型默认配置
- 全局默认 provider 改为 `deepseek`（v4-flash）
- DeepSeek 模型列表更新为 `deepseek-v4-flash` / `deepseek-v4-pro`（旧 deepseek-chat/reasoner 2026-07-24 弃用）
- `anthropic_compat`（Karl-5）仅保留 opus-4-8 / sonnet-4-6，去掉 haiku

### 其他
- 开发文档版本号改为与项目版本对齐（`开发文档_v0.2.0.md`，旧 v1/v2 保留为历史）
- prompts.py 横版脉络提示词：18-30 节点、4 层深度、关系标签（支撑/实证/递进/对比/因果/博弈/例证）

## v0.1.0 — 2026-06-14

### P1 核心处理流水线（商学院板块）
- 处理引擎 `pipeline/engine.py`：`run_stream()` 生成器按步执行、SSE 流式推进度、产出落盘（带版本）
- 转写解析 `pipeline/transcript.py`：时间戳识别（[h:mm:ss]/SRT 等）、10 分钟分桶、按句切块
- 四步提示词 `modules/business_school/prompts.py`：逐字稿纠错 / 章节稿 / 七节纪要主体 / Mermaid 知识图谱，全程注入「补充背景」
- 逐字稿分块**并行纠错**（默认 6 路）；长逐字稿章节/纪要走 map-reduce
- API：`GET /sessions/{id}/run-stream`（SSE 进度）+ `GET /artifacts/{name}`
- 前端工作台：「确认生成」真正工作——进度条、精炼/详尽切换、产出标签页（✓ 标记）
- 产出渲染 `OutputView.jsx`：Markdown（marked）+ Mermaid 图（含源码切换）

### 实测验证
- 带时间戳样例转写稿端到端跑通：口水词清理、DCF/WACC 英文保留、10 分钟分桶、七节纪要、合法 Mermaid 图（连线标注 支撑/因果/递进/对比）

### 其他
- health phase 标记为 P1；清理空的 prompts/ 残留目录

## v0.0.2 — 2026-06-14

### 新增
- 第三方 Anthropic 兼容中转站 Provider（`anthropic_compat`，Messages 原生格式，四要素配置）
- OpenAI 兼容 Provider 升级为四要素配置（Provider 名 / url / apikey / model + 视觉开关）
- 工作台支持上传输入素材（txt/pdf/图片）与保存「补充背景 & 重点要求」

### 修复
- 全局 `DEFAULT_MODEL` 串台 bug：原会把默认模型名发给所有 provider，现各 provider 用自身默认模型
- 中转站经 Cloudflare 被 403「Your request was blocked」：中转站 Provider 自动伪装浏览器 UA 绕过 WAF

### 优化
- UI 改为 Anthropic 官网风格（暖米白底 + 珊瑚色点缀 + 衬线标题）
- 交互结构调整：新建项目仅需名字（默认「新项目」）；背景信息移入工作台；
  工作台自上而下为 输入素材 → 补充背景 → 确认生成 → 输出区

## v0.0.1 — 2026-06-14

### 完成
- P0 脚手架：项目骨架、后端（FastAPI + 模型 Provider 抽象层 + 会话存储 + 板块注册表）、前端（Vite + React 壳）
- 模型可切换（默认 Claude，支持 DeepSeek / 通用 OpenAI 兼容端点）
- 商学院板块（"学堂"）开放；矿山/闲谈/通用板块占位预留
- 前端板块首页（建会话 + 任务前自定义提示词）、工作台（四产出标签页骨架）
- 本地存储：会话元数据 + 产出版本管理（为 P2 迭代修订打底）
- 文档体系：版本化开发文档（v1/v2）+ 开发日志

### 下一步（P1）
- 核心处理流水线：txt 转写稿 → 逐字稿 → 章节稿 → 纪要主体 → 知识图谱（Mermaid）
- SSE 进度流式推送
