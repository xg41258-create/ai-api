# 后端说明（backend）

本目录包含多模态 AI 后端服务（FastAPI + SSE）的实现与测试工具。

## 快速开始

1. 进入目录并激活虚拟环境（Windows PowerShell）：

```powershell
cd backend
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. 启动服务：

```powershell
# 单进程（推荐用于生产测试）
d:/api-test/my-ai-app/backend/venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001

# 开发时可加 --reload
# d:/.../venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

## 主要端点

- `GET /health` — 健康检查（检查 DB 连通性）。
- `POST /chat` — POST 型 SSE 流（请求体 JSON：`{ "text": "...", "session_id": "...", "image": "base64..." }`）。
  - 响应为 Server-Sent Events：
    - `event: meta`（首条，包含 `request_id`/`trace_id`/`session_id`）
    - 多个 `data: ...` 分块（模型增量输出）
    - `id: <message_id>`（保存后的 assistant 消息 id，可用于断点续传）
    - `event: done`（结束标记）
- `GET /chat_stream` — GET 型 SSE（query string：`?text=...&session_id=...`）。支持 `Last-Event-ID`（HTTP header）重放：服务会在流开始前重放 `id > last_event_id` 的历史消息。
- `GET /history/{session_id}` — 返回该 session 的历史消息数组。
- `GET /logs/{level}?lines=N` — 读取日志片段，`level` 可选 `info|error|all`。
- `GET /metrics` — Prometheus 指标（`prometheus-client`），示例指标：`backend_http_requests_total`、`backend_sse_streams_total`、`backend_openai_stream_errors_total`。

## SSE 客户端示例

项目已包含静态示例页面：

- [EventSource 示例页面](/ui/eventsource_client.html)（浏览器打开 `http://127.0.0.1:8001/ui/eventsource_client.html`）

最简 JS 使用示例：

```javascript
const es = new EventSource('/chat_stream?text=你好&session_id=test123');
es.addEventListener('meta', e => console.log('meta', JSON.parse(e.data)));
es.onmessage = e => console.log('data', e.data);
es.addEventListener('done', e => console.log('done'));
es.addEventListener('error', e => console.error('sse error', e));
```

示例页面实现了指数退避、最大重连、Stop 控制和 Last-Event-ID 恢复策略，推荐先用该页面做手工验证。

## 测试与工具

- `simulate_disconnect.py` — 自动化脚本：建立 SSE 连接并在 5s 后模拟客户端断开，用于验证服务端对断开的处理与重连行为。
  - 运行：

```powershell
cd backend
d:/api-test/my-ai-app/backend/venv/Scripts/python.exe simulate_disconnect.py
```

- `kill_port_8001.py` — 帮助查找并结束占用 8001 的进程（用于本地调试）。

## 日志

- 位置：`backend.log` / `info.log` / `error.log`（轮转写入），以及 `backend.json.log`（结构化 JSON，用于日志聚合）。
- 建议：对接日志聚合平台时使用 `backend.json.log`。
- 在线查看：`GET /logs/all?lines=200`。

日志中会自动注入 `request_id`、`trace_id`（通过 `X-Request-ID` / `X-Trace-ID` header 传播）以及 `session_id`（当上下文可用）以便追踪。

## 指标 (Prometheus)

- `GET /metrics` 暴露 Prometheus 指标，可在 Prometheus 中配置 scrape target:

```
- job_name: 'my-ai-app'
  static_configs:
    - targets: ['127.0.0.1:8001']
```

## 断点续流（Resume）策略

- 服务在 SSE 首次建立时会发送 `event: meta`（包含 `request_id`/`trace_id`/`session_id`）。
- 当客户端重连时可设置 `Last-Event-ID` header（或 `EventSource` 库内部发送的 lastEventId）来告诉服务从哪个 `id` 之后的消息需要重放。
- 服务会在重连后先推送 id > last_event_id 的历史消息，然后再继续流式合成输出。

## 常见操作

- 手动查看 OpenAPI：`http://127.0.0.1:8001/docs`
- 下载日志包（若存在）：`backend/logs_bundle.zip`

## 备注

- 已实现：OpenAI 流式创建的重试与指数退避、SSE meta 注入、结构化日志、Prometheus 指标、自动化断连测试脚本。后续可扩展：更精细的中间续流（基于 token/offset）、把 JSON 日志推送到外部聚合。

---

文件列表：

- [backend/main.py](main.py)
- [backend/simulate_disconnect.py](simulate_disconnect.py)
- [backend/eventsource_client.html](eventsource_client.html)
- [backend/requirements.txt](requirements.txt)

