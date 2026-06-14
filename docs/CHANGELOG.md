# Changelog

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
