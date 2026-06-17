# Changelog

## v0.6.2 — 2026-06-17

### 修复：老会话版本历史可见性 + 纲目截断放行

- **历史版本恢复显示**：老会话的版本目录仍在旧产出名下（如 `逐字稿.md`、`章节稿.md`），但前端按新产出名（`实录.md`、`纲目.md`）查询，导致历史版本看似消失。现 API 合并旧名版本 + 新名版本为连续版本线，查看、下载、恢复都按连续版本号正确映射回真实文件。
- **纲目完整性闸门**：修复最终汇编低于本堂课预算区间、甚至出现空标题（如 `### 阶段20：`）时仍被当作成功结果落盘的问题。最终汇编输出上限 7000 → 12000，并新增完整性检查；不达标时优先回退完整候选，仍不达标则报错而不覆盖当前产出。
- **数据处理**：已用新代码重新生成“中美科技核心差异”的纲目，最新版本为合并版本线 v4，共 26 个阶段，覆盖到 03:01:20。
- **验证**：后端 19 项 unittest 全过。

## v0.6.1 — 2026-06-17

### 导出能力 MVP：单产出下载 + 项目 ZIP 打包

根据当前真实测试数据以「转写文本 + 课堂笔记图片」为主的情况，先把导出工作提前；PDF 抽文本继续顺延到有真实 PDF 样例后再做。

#### 新功能
- **单个产出下载**：实录、纲目、撷要、笺注、脉络均可从当前产出面板直接下载，保留原始 `.md` / `.mmd` 文件名。
- **历史版本下载**：查看某个历史版本时，按钮切换为「下载此版本」，文件名自动追加 `_vN`。
- **项目打包导出**：产出区新增「打包全部」，将当前项目所有已生成产出打包为 ZIP；只包含产出，不包含原始转写稿、图片等输入素材。

#### API
- `GET /api/sessions/{sid}/artifacts/{name}/download`
- `GET /api/sessions/{sid}/artifacts/{name}/versions/{version}/download`
- `GET /api/sessions/{sid}/exports/bundle`

#### 验证
- 新增 4 项导出单测：当前产出下载、历史版本下载、ZIP 只包含生成产出、空项目导出报错。
- 后端 17 项 unittest 全过；前端构建通过。

#### 下一步
- 导出增强：合并 Markdown、DOCX/PDF 导出、脉络图 SVG/PNG 导出。
- PDF 抽文本在有真实 PDF 测试样例后接入。

## v0.6.0 — 2026-06-16

### P3 辅助素材（一）：课堂笔记照片视觉识别

把"能传不能用"的图片素材真正接入生成：上传的课堂笔记/板书/讲义照片由视觉模型转录成文字，纳入纪要生成。

#### 图片视觉链路（新打通）
- **Provider 多模态**：`Message` 新增可选 `images` 字段（`ImagePart` 携带 base64）；Anthropic（image block）与 OpenAI 兼容（image_url data URI）两套 `chat()` 在带图时拼多模态 content，无图时行为完全不变。
- **笔记转录**：新增 `pipeline/vision.py`——图片经视觉模型转成 Markdown（手写/板书/PPT/讲义；公式转 LaTeX、表格转 Markdown、字迹不清标 `[字迹不清]`、不杜撰）。大图用 Pillow 降采样到 1568px（Pillow 缺失时优雅降级原图）。
- **按文件缓存**：转录结果按「文件名 + mtime」缓存到 `derived/notes/`，重复生成不重复调用；重新上传（mtime 变）自动失效。

#### 定位：笔记照片 = 辅助素材
- 转录文本作为【课堂笔记补充素材】注入 **撷要 / 笺注**，用于补全讲授一带而过、但笔记里记下的要点/公式/数据；与逐字稿冲突时以逐字稿为准、不据此杜撰。
- **不混入实录**（实录是讲话逐字稿，照片不是讲话）；也**暂不进纲目**（纲目是按时间轴的骨架，照片无时间戳，避免污染时间对位）。

#### 图片识别模型可手动指定（应用户要求）
- 模型配置面板底部新增「**图片识别模型**」选择器：可手动指定供应商 + 模型，或留「自动」。
- 自动时选第一个【已配置且支持视觉】的供应商（全局默认优先）；一个都没有则跳过图片并在进度里提示去配置。
- 新增 `GET /api/providers`（返回 `vision`）+ `PUT /api/vision-model`；落盘 `providers.json` 的 `vision_id/vision_model`（缺字段向后兼容为"自动"）。

#### 前端
- 输入素材区图片行加 📷 标识；上传 accept 增加 webp；文案改为"转写文字稿（主体）+ 课堂笔记照片（辅助）"。
- 生成撷要/笺注时进度提示"识别 N 张笔记照片 / 复用缓存 / 识别失败"。

#### 验证
- 新增 8 项单元测试：多模态消息构造（含/不含图）、图片列举与 media type、笔记缓存 mtime 失效、无视觉模型提示、转录与缓存复用。13 项后端测试全过。

#### 取舍 / 下一步
- PDF 抽文本按用户排序顺延（pypdf 已就位，随时补）。
- 笔记暂只进撷要/笺注；如需进纲目可后续按"补充参考"接入汇编步。

## v0.5.4 — 2026-06-15

### 纲目阶段数按课堂长度动态收敛

- 纲目不再让每个 6000 字分段自由细分，改为先按课堂时长计算整堂课目标阶段数：约每 6 分钟一个主阶段，最终硬边界为 20-50 个；无可靠时间戳时按正文长度估算。
- 各并行分段按长度分配明确的候选阶段配额，配额总和等于全局目标，避免分段越多、最终阶段数越失控。
- 新增一次全局汇编：合并分段边界上重复或属于同一论证链的相邻阶段，避免把单个例子、简短问答和补充说明提升成独立阶段。
- 汇编结果若超出目标浮动区间会自动纠偏一次；阶段编号继续统一重排，时间区间继续取自原文。
- 新增 5 项纲目预算单元测试。真实“中美创新与商业战略”原稿由旧版 121 阶段调整为目标约 30 阶段（允许 26-34）。

## v0.5.3 — 2026-06-15

### 生成提速与提质：并行 + 长上下文整篇成稿 + 分步默认模型

#### 速度
- **撷要 / 笺注 改整篇一次成稿**：去掉 map-reduce，把完整实录(+纲目)一次喂给模型（依赖现代模型长上下文）。长稿从多次调用降到 1 次，更快且不丢跨段上下文。移除 `SUMMARY_SINGLE_LIMIT`/`SUMMARY_PART_CHARS` 两段式逻辑。
- **纲目分段改并行**：各段用线程池并发生成（`_parallel_map`，上限 `MAX_PARALLEL=6`），不再串行；编号仍统一重排为连续 1..K。
- **实录分块 2800 → 10000 字**：6.3 万字稿块数 16 → 7，调用更少、跨块割裂更少、对高并发返空的中转更友好；同步把单块输出上限提到 16000 token（v4-flash 支持 384K 输出，余量充足）。

#### 质量
- 撷要/笺注看到全篇 → 跨段综合更好、无"要点的要点"信息损耗。
- 笺注输出上限 4096 → 8000 token，详尽版不易截断。
- 纲目保留 v1 细颗粒度（仍分段），不受影响。

#### 分步默认模型（保留用户手动切换权）
- 实录 / 纲目 默认 **deepseek-v4-flash**（量大、偏体力活，用快模型）；撷要 / 笺注 / 脉络 默认 **claude-sonnet-4-6**（偏归纳推理，用强模型）。
- `StepDef` 新增 `default_model`；后端 `engine._step_default` 按"首选模型名"在【已配置】供应商里自动匹配（全局默认 provider 优先），不写死供应商 id；前端 `Workbench.stepDefault` 同逻辑展示。
- 完全保留每个产出处手动切换 provider/model 的下拉，仅"未手动选时"的默认值变聪明。

#### 取舍
- 撷要/笺注不再有 map-reduce 兜底：超出所选模型上下文时由 provider 直接报错（用户改用大上下文模型即可），不静默降级。

#### 规划（记入开发文档下一步候选）
- 生成用时记录；实录分块截断保护（`finish_reason==length` 自动二分重切 + 超长稿提高并发）。

## v0.5.2 — 2026-06-15

### 纲目解除对实录的依赖，支持与实录并行生成

- **去掉纲目的执行依赖** `requires=["transcript"]`：纲目本就基于转写原文独立归纳，不读实录内容。此前那道门禁会逼用户先等实录跑完才能生成纲目；现解除后，实录与纲目可【并行触发】（runner 本就按 `(sid, step)` 各自并发），两个长耗时步骤同时跑、省一半等待。
- `_step_chapters` 增加空输入保护：纲目可能先于实录运行，未上传转写原文时给出明确报错而非空跑。
- 前端无需改动：纲目 Tab 灰态动态读 `step.requires`，后端去掉后自动解锁（仍要求已上传素材）。
- 撷要 / 笺注仍依赖实录、脉络仍依赖撷要/笺注（真内容依赖，保留）。

## v0.5.1 — 2026-06-15

### 修复 v0.5.0 的实录碎片化 / 残留 与 纲目过粗

#### 实录（重做发言人处理 → B精简版·角色轻标注）
- **不再相信原稿的机器发言人编号**（ASR 常把同一个人打成不同编号，导致连续发言被切碎）。切分改回按句子长度分块，由模型**按语义**合并同一人连续发言、仅在确有切换处用**角色**轻标注（`**讲师：**`/`**提问：**`/`**回答：**`），课堂讲授大段直接出连贯正文。
- **修复"部分段落残留发言人/时间戳"**：根因是部分分块模型返回空内容，旧逻辑把**原始块**直接兜底灌入。现在：① 空返回/报错会**重试**（每块至多 3 次）；② 送模型前用 `strip_timestamps` **确定性去时间戳**（保留标签作弱提示）；③ 仍失败则用 `clean_fallback`（去标签+时间戳的纯正文）兜底，**绝不回退到带标记的原文**；④ 有降级块时在进度里提示可单独重生成。
- 分块大小 1800 → 2800，降低并发块数、利于同一人发言合并。

#### 纲目（回到 v1 的细颗粒度，并修正编号与时间）
- 改回 **v1 的"分段生成"**：把**带时间戳的原始稿**按段输入、每段**细致划分**多个阶段（颗粒度细），再把各段拼接后**统一重排阶段编号**为连续 1..K。
- 既找回 v1 的细分割，又修掉 v1 的两个毛病：阶段编号不再乱（重排）、时间区间从原始稿就地读取更准（不臆造、无对应留空）。
- 移除 v0.5.0 的"全程 digest 单次成稿"（过粗）与无时间戳长稿的二次摘要主路径。

#### 内部
- `pipeline/transcript.py`：新增 `strip_timestamps` / `clean_fallback` / `has_timestamps`；移除 `build_timeline_digest`、说话人轮次切分（`parse_turns`/`CorrectionUnit` 等）。
- `pipeline/engine.py`：新增 `_correct_chunk`（重试+兜底）/ `_renumber_stages`；`_make_chapters` 改分段+重排；`_assemble_transcript` 不再回退原文。
- `modules/business_school/prompts.py`：`transcript_system` 改 B精简版角色轻标注；新增 `chapters_segment_system`（分段细分、段内从 1 编号）。

## v0.5.0 — 2026-06-15

### 实录分发言人 · 纲目连续化与时间对位 · 状态徽标随进度

#### 产出优化
- **实录：区分发言人、保留连续发言完整**。转写稿改为按「**说话人轮次**」切分纠错单元——同一发言人一段时间内的连续发言**永不在中间被切断**；不同发言人之间空行明显区隔，每位发言人以加粗标签 `**发言人X：**` 起段。说话人识别兼容 `发言人1`/`发言人 1`/`主持人：`/`发言人2：00:01:10`/`主持人 00:02:00`/`Speaker 1:`/`A：` 等格式，并规避「老师讲得好」这类裸角色词误判；时间戳统一去除。
- **纲目：阶段连续化 + 时间从原文对位**。长稿改走 map-reduce（先分段提炼话题脉络、再单次汇编），消除「阶段 1–6 又从 1–5」的碎片化；阶段编号贯穿全程连续。因实录已去时间戳，阶段时间区间改从**原始转写稿**逐行提取时间锚点（行首或说话人标签后均可识别）对位标注。

#### 体验
- 右上角状态徽标随开发进度更新为 **P2**，并显示当前版本号（`后端在线 · v0.5.0 · P2`）。`/health` 增加 `version` 字段（读取根 `VERSION`，单一版本源）；`FastAPI(version=…)` 同源。

#### 规划
- 开发文档新增 **P5 智能问答（课堂答疑）** 计划：独立问答窗口，AI 据素材 + 五类产出回答用户提问；排在 P3 辅助素材之后落地。

#### 内部
- `pipeline/transcript.py`：新增 `parse_turns` / 重写 `build_correction_units`（说话人轮次驱动）/ `build_timeline_digest` 改逐行扫描；移除 `parse_segments`/`group_10min`/`Bucket` 等死代码。
- `pipeline/engine.py`：新增 `_make_chapters`（map-reduce）；`_assemble_transcript` 支持 `is_continuation` 无缝拼接。
- `modules/business_school/prompts.py`：`transcript_system` 改为分发言人排版；`chapters_system/chapters_user` 改为连续阶段 + 时间轴参考对位。

## v0.4.0 — 2026-06-15

### P2 持续迭代修订：产出可反复打磨 + 版本历史

#### 新功能
- **持续迭代修订**：每个产出（实录/纲目/撷要/笺注/脉络）下方常驻修订框，填入修订意见后基于**当前版本内容 + 意见**再生成，结果存为**新版本**，版本 note 即修订意见。
- **版本切换器**：产出有 ≥2 版本时出现 v1/v2/… 标签（当前版高亮），可查看任意历史版本（只读，并显示当时的修订意见）。
- **恢复历史版本**：查看旧版本时可「恢复此版本为当前」（写成一个新版本，历史不抹除）。
- 修订复用既有 runner / SSE 进度通道；同一产出同一时间只允许一个任务（生成或修订）。

#### API
- `POST /api/sessions/{id}/revise-step?step=X` body `{instruction}`：启动单步修订（幂等，进度复用 `run-step-stream`）
- `POST /api/sessions/{id}/artifacts/{name}/versions/{v}/restore`：恢复某历史版本为当前

#### 提示词与安全
- 新增通用修订提示词（`prompts.revise_system/revise_user`）：保持原产出格式与结构、只改意见涉及处；脉络图特例仍输出 `flowchart LR` Mermaid。
- 安全红线沿用：不改动数字/金额/日期/人名/公司名。实测修订「8.5%」等数字被正确保留。

#### 内部
- `pipeline/engine.py` 新增 `revise_one_step`；`runner.py` 抽出通用 `_start`，新增 `start_revise`。

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
