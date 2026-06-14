# 会议纪要生成平台

**v0.0.1** · 多场景会议纪要生成平台（本地优先）。当前开发：**商学院课堂讲座**板块。

- 方案与架构：[docs/开发文档_v2.md](docs/开发文档_v2.md)（最新版；历史版本保留在 docs/）
- 变更记录：[docs/CHANGELOG.md](docs/CHANGELOG.md)
- 沟通时间线：[docs/开发日志.md](docs/开发日志.md)
- 需求背景：[docs/任务背景.md](docs/任务背景.md)

## 快速开始（开发态）

### 后端
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 填入 ANTHROPIC_API_KEY 等
uvicorn main:app --reload --port 8000
```
健康检查：打开 http://localhost:8000/api/health

### 前端
```bash
cd frontend
npm install
npm run dev                 # 开发服务器，默认 http://localhost:5173
```

> 运行时（生产）：前端会打包为静态文件由后端直接托管，届时**只需启动后端**即可使用。

## 目录
```
backend/    FastAPI 后端：providers(模型层) / modules(板块) / pipeline / revision / storage
frontend/   Vite + React 前端
docs/       项目文档（开发文档各版本、开发日志、需求背景、变更记录）
scripts/    工具脚本（版本号管理等）
data/       本地会话数据（git 忽略，不外发）
```

## 安全红线
- 不改动数字、金额、日期、人名、公司名（涉数字的模型改动入疑点清单）。
- 原始转写稿与 `data/` 不提交 Git、不外发。
