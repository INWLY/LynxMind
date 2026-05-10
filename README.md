# LynxMind - 智能 RAG 资讯与 Agent 对话平台

LynxMind 是一款独立开发的全栈 AI 应用，集成了"自动化资讯聚合、向量检索、Agent 深度对话"等功能。基于 RAG 架构，旨在超越简单的事实陈述，为用户提供高准确率的行业洞察与多模态分析。

## 🌟 核心亮点
* **全栈架构与 RAG 闭环**：基于 FastAPI 与 LangChain 搭建底层 Agent，打通自动化抓取、文档向量化到意图识别与多工具调用的完整业务链路。
* **全局锚点机制与长时记忆**：首创"资讯锚点（Context Anchor）"注入策略，约束模型讨论边界；结合大模型动态摘要机制压缩会话历史，优化 Token 消耗。
* **Agent 链路白盒化与容灾**：深度重构 SSE 流式管道，将思维链路实时透传至前端；并实现基于双模型的 Fallback 降级机制，保障系统高可用。
* **现代沉浸式 UX/UI**：Vue 3 驱动的响应式界面，采用乐观更新（Optimistic UI）策略与极简卡片流设计。

## 🛠️ 技术栈
* **后端**: Python 3, FastAPI, LangChain, SQLAlchemy
* **前端**: Vue 3, HTML5/CSS3, Server-Sent Events (SSE)
* **AI/数据**: LLM Agent (OpenAI 兼容), 向量检索 (Vector DB), Web Scraping

## 🚀 快速启动

### 1. 克隆仓库
```bash
git clone https://github.com/INWLY/LynxMind.git
cd LynxMind
```

### 2. 后端配置
在项目根目录创建 `.env` 文件并填入你的 API Key：
```env
ARK_API_KEY=your_api_key_here
MODEL=your_model_name
BASE_URL=your_base_url
```

#### 启动基础设施依赖（PostgreSQL、Redis、Qdrant）
```bash
docker compose up -d
```

#### 安装 Python 依赖并启动
```bash
# 安装依赖（推荐使用 uv）
uv sync

# 启动后端服务
uv run uvicorn backend.app:app --reload
```

### 3. 前端访问
后端启动后，浏览器访问：
- 前端页面：`http://127.0.0.1:8000/`
- API 文档：`http://127.0.0.1:8000/docs`
