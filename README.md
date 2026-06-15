# 会议纪要生成平台

**v0.5.0** · 多场景会议纪要生成平台（本地优先）。当前开放：**商学院课堂讲座**板块。

- 方案与架构：[docs/开发文档_v0.5.0.md](docs/开发文档_v0.5.0.md)（最新版；历史版本保留在 docs/）
- 变更记录：[docs/CHANGELOG.md](docs/CHANGELOG.md)
- 沟通时间线：[docs/开发日志.md](docs/开发日志.md)
- 需求背景：[docs/任务背景.md](docs/任务背景.md)

## 功能概览

上传课堂转写稿（txt / pdf / 图片），一键生成五类产出：

| 产出 | 说明 |
|---|---|
| 实录 | 纠错后的逐字稿（口水词清理、专业词保留；区分发言人、不切断连续发言、去时间戳） |
| 纲目 | 章节结构稿（阶段连续编号，时间区间从原文时间戳对位） |
| 撷要 | 精炼版会议纪要 |
| 笺注 | 详尽版会议纪要（七节结构） |
| 脉络 | Mermaid 知识图谱（矢量，可缩放） |

每个产出可**独立生成**、**独立选模型**、**持续迭代修订**（按意见再生成新版本、保留版本历史、可恢复旧版），支持 Claude、DeepSeek、自定义 OpenAI 兼容端点。

## 快速启动

### 生产模式（推荐，只需一条命令）

前端已构建到 `frontend/dist/`，后端直接托管：

```bash
# 如前端代码有更新，先重新构建
cd frontend && npm run build && cd ..

cd backend
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000 即可使用。

### 开发模式（前后端分离）

**后端：**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 填入 API Key（见下方配置说明）
uvicorn main:app --reload --port 8000
```

**前端（另开终端）：**
```bash
cd frontend
npm install
npm run dev                 # 默认 http://localhost:5173
```

## 模型配置

**日常配置走界面**：启动后点右上角 **⚙ 模型配置**，即可随时新增/编辑/删除供应商、填 url/apikey/模型、设默认。配置持久化在 `data/providers.json`（git 忽略，含密钥不外发）。

`backend/.env` **仅用于首次初始化**：第一次启动且 `data/providers.json` 不存在时，从 .env 播种出 Claude / DeepSeek / OpenAI 兼容 / Anthropic 中转站四个供应商；之后所有改动以面板（providers.json）为准，改 .env 不再生效（除非删掉 providers.json 重新播种）。

首次播种可填的字段见 [backend/.env.example](backend/.env.example)，常用：

```env
ANTHROPIC_API_KEY=sk-ant-...     # Claude 官方
DEEPSEEK_API_KEY=...             # DeepSeek
DEFAULT_PROVIDER=deepseek        # 首次播种时的默认供应商
```

## 目录结构

```
backend/
  main.py          FastAPI 入口（开发态 CORS + 生产态静态托管）
  config.py        环境变量配置（pydantic-settings，仅首次播种用）
  api/             路由层
  providers/       模型层：base/claude/openai_compat（调用基类）+ dynamic（按配置构建）+ store（持久化）+ registry
  modules/         板块（business_school：商学院讲座）
  pipeline/        处理引擎（engine / runner / transcript）
  storage/         会话存储（含产出版本管理）
frontend/
  src/             Vite + React 前端
  dist/            构建产物（后端直接托管）
docs/              项目文档（开发文档各版本、开发日志、需求背景、变更记录）
scripts/           工具脚本（版本号管理等）
data/              本地会话数据 + providers.json（git 忽略，不外发）
```

## 安全红线

- 不改动数字、金额、日期、人名、公司名（涉数字的模型改动入疑点清单）。
- 原始转写稿与 `data/` 不提交 Git、不外发。
