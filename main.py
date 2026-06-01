import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
import uvicorn
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Response, Header, Request  # 💡 引入了 Depends
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from openai import OpenAI
import json
import random
import traceback
import uuid
import contextvars
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

# 引入 SQLAlchemy 核心组件
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ================= 配置区域 =================
load_dotenv()

# 日志配置
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# 文件日志（轮转）
log_dir = os.path.dirname(__file__)
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "backend.log")
info_log = os.path.join(log_dir, "info.log")
error_log = os.path.join(log_dir, "error.log")
try:
    root_logger = logging.getLogger()
    # 全量日志
    file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root_logger.addHandler(file_handler)

    # 只包含 INFO / WARNING（低于 ERROR）的处理器
    class BelowFilter(logging.Filter):
        def __init__(self, max_level):
            super().__init__()
            self.max_level = max_level
        def filter(self, record):
            return record.levelno < self.max_level

    info_handler = RotatingFileHandler(info_log, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    info_handler.setLevel(logging.INFO)
    info_handler.addFilter(BelowFilter(logging.ERROR))
    info_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root_logger.addHandler(info_handler)

    # 只包含 ERROR 及以上的处理器
    error_handler = RotatingFileHandler(error_log, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root_logger.addHandler(error_handler)

    logging.info(f"日志写入: {log_file}, {info_log}, {error_log}")

    # 结构化 JSON 日志（另外输出到独立文件，便于日志聚合/分析）
    try:
        json_log = os.path.join(log_dir, "backend.json.log")
        json_handler = RotatingFileHandler(json_log, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                payload = {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "level": record.levelname,
                    "message": record.getMessage(),
                    "module": record.module,
                    "funcName": record.funcName,
                    "line": record.lineno,
                    # 可选的上下文字段（如果存在）会被自动注入
                    "request_id": getattr(record, "request_id", None),
                    "trace_id": getattr(record, "trace_id", None),
                    "session_id": getattr(record, "session_id", None),
                }
                if record.exc_info:
                    payload["exc"] = self.formatException(record.exc_info)
                return json.dumps(payload, ensure_ascii=False)
        json_handler.setFormatter(JsonFormatter())
        root_logger.addHandler(json_handler)
        logging.info(f"结构化日志写入: {json_log}")
    except Exception:
        logging.exception("无法创建结构化 JSON 日志处理器")
except Exception:
    logging.exception("无法创建文件日志处理器")

# 全局 request/trace 上下文（用于在日志/事件中关联请求）
request_id_ctx = contextvars.ContextVar("request_id", default=None)
trace_id_ctx = contextvars.ContextVar("trace_id", default=None)


class ContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_ctx.get()
        record.trace_id = trace_id_ctx.get()
        return True

root_logger.addFilter(ContextFilter())

# Prometheus 指标（简单示例）
REQUEST_COUNTER = Counter("backend_http_requests_total", "Total HTTP requests", ["method", "endpoint"])
SSE_COUNTER = Counter("backend_sse_streams_total", "Total SSE streams started")
OPENAI_ERRORS = Counter("backend_openai_stream_errors_total", "OpenAI stream errors")

API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
# 从环境读取端口并保证为整数
PORT = int(os.getenv("PORT", "8001"))

# 🔒 从本地 .env 文件中自动读取数据库连接
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://username:password@localhost:5432/neondb")
# ===========================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 将当前 backend 目录挂载为静态目录，便于直接访问示例页面（例如 /ui/eventsource_client.html）
static_dir = os.path.dirname(__file__)
app.mount("/ui", StaticFiles(directory=static_dir), name="ui")


@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    # 从客户端继承 request id（如果有），否则生成新的
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    trace = request.headers.get("X-Trace-ID") or str(uuid.uuid4())
    request_id_ctx.set(rid)
    trace_id_ctx.set(trace)
    logging.info(f"→ START {request.method} {request.url.path} request_id={rid} trace_id={trace}")
    response = await call_next(request)
    # 将 id 回传给客户端，便于关联日志和 SSE 恢复
    response.headers["X-Request-ID"] = rid
    response.headers["X-Trace-ID"] = trace
    logging.info(f"← END {request.method} {request.url.path} request_id={rid} trace_id={trace} status={response.status_code}")
    return response

# ================= 数据库模型初始化 =================
engine = create_engine(
    DATABASE_URL,
    connect_args={"sslmode": "require"},  # ✨ 核心修复：在驱动层强制开启 SSL，彻底解决 Neon 主动断连问题
    pool_pre_ping=True,                   # 断线自动重连检测
    pool_recycle=60,                      # 60秒空闲自动回收连接，完美适配 Serverless 数据库
    pool_size=5,
    max_overflow=10
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    role = Column(String)
    content = Column(Text) 

# 💡 新增：FastAPI 依赖注入——安全的数据库会话生命周期管理生成器
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

logging.info("\n⏳ 正在连接 Neon 云数据库...")
for attempt in range(1, 6):
    try:
        # 尝试执行建表/连接
        Base.metadata.create_all(bind=engine)
        logging.info("✅ [Database] 数据库连接成功，表结构同步完成！\n")
        break
    except Exception as e:
        if attempt == 5:
            logging.exception("❌ [Database] 连续 5 次连接数据库失败，程序即将崩溃。请检查 .env 中的连接字符串。")
            raise e
        logging.warning(f"⚠️ [Database] Neon 数据库正在从休眠中唤醒 (第 {attempt}/5 次尝试)... 3秒后将自动重试...")
        time.sleep(3)  # 给云端数据库 3 秒钟的开机时间
# ===================================================

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)


def openai_create_stream_with_retry(model, messages, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """Call OpenAI streaming API with retries and exponential backoff.

    Returns the streaming iterator from the SDK or raises the last exception.
    """
    attempt = 0
    last_exc = None
    while attempt < max_retries:
        try:
            return client.chat.completions.create(model=model, messages=messages, stream=True)
        except Exception as e:
            last_exc = e
            attempt += 1
            if attempt >= max_retries:
                break
            # exponential backoff with jitter
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            jitter = random.uniform(0, delay * 0.3)
            wait = delay + jitter
            logging.warning(f"OpenAI streaming create failed (attempt {attempt}/{max_retries}), retrying in {wait:.1f}s: {e}")
            time.sleep(wait)
    # no more retries
    logging.exception("OpenAI streaming create ultimately failed")
    raise last_exc

class Query(BaseModel):
    text: str
    session_id: str
    image: Optional[str] = None 

# 🛠️ 修复后的历史记录获取接口
@app.get("/history/{session_id}")
async def get_history(session_id: str, db: Session = Depends(get_db)):
    try:
        # 使用 Depends(get_db) 自动管理生命周期并提取数据
        rows = db.query(Message).filter(Message.session_id == session_id).order_by(Message.id.asc()).all()
        
        # 显式提取为标准字典列表，防止跨线程异步转换引发的 500 序列化报错
        return [{"role": r.role, "content": r.content} for r in rows]
    except Exception as e:
        logging.exception("\n❌ 【获取历史记录失败】真实原因:")
        # 即使报错也返回空列表，避免前端界面崩溃报红字
        return []


@app.get("/health")
async def health(db: Session = Depends(get_db)):
    """简单健康检查：检查数据库是否可用"""
    try:
        # 快速查询以确认数据库联通
        _ = db.query(Message).limit(1).all()
        return {"status": "ok", "db": "ok"}
    except Exception:
        logging.exception("健康检查失败：数据库不可用")
        raise HTTPException(status_code=503, detail="db unreachable")

# 🛠️ 修复后的智能聊天接口
@app.post("/chat")
async def chat_with_ai(query: Query, db: Session = Depends(get_db)):
    # 记录请求指标
    try:
        REQUEST_COUNTER.labels(method="POST", endpoint="/chat").inc()
        clean_text = query.text.strip()
        if not clean_text:
            clean_text = "请帮我分析和识别这张图片。"

        # 1. 捞取历史记录
        rows = db.query(Message).filter(Message.session_id == query.session_id).order_by(Message.id.asc()).all()
        
        now = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
        
        # 2. ✨ 智能识别：检查是否真的上传了有效的 base64 图片
        has_valid_image = False
        if query.image and query.image.strip() and query.image.strip() != "string":
            has_valid_image = True

        # 根据是否有图，智能分配系统提示词和选择对应的模型
        if has_valid_image:
            system_prompt = f"当前系统时间：{now}。你现在是一个先进的多模态 AI 助手，能够同时理解文字和用户上传的图片。"
            selected_model = "glm-4v-flash"  # 传图时使用多模态模型
        else:
            system_prompt = f"当前系统时间：{now}。你现在是一个先进的 AI 智能助手。"
            selected_model = "glm-4-flash"   # 纯文本时使用极速文本模型

        history = [{"role": "system", "content": system_prompt}]
        for row in rows:
            history.append({"role": row.role, "content": row.content})
        
        # 3. 智能组装请求体
        if has_valid_image:
            img_data = query.image.strip()
            if not img_data.startswith("data:image"):
                image_url = f"data:image/jpeg;base64,{img_data}"
            else:
                image_url = img_data

            current_user_content = [
                {"type": "text", "text": clean_text},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
            db_content = f"[上传了图片] {clean_text}"
        else:
            current_user_content = clean_text
            db_content = clean_text

        # 4. 存入用户消息至数据库，并刷新以确保后续可用
        user_msg = Message(session_id=query.session_id, role="user", content=db_content)
        db.add(user_msg)
        db.commit()
        try:
            db.refresh(user_msg)
        except Exception:
            # refresh 不是必须的，只是尝试保证对象最新
            pass

        history.append({"role": "user", "content": current_user_content})
        
    except Exception as e:
        db.rollback()
        print(f"\n❌ 【聊天接口数据库操作失败】真实原因:\n{e}\n")
        raise HTTPException(status_code=500, detail=str(e))

    def generate():
        try:
            # SSE 指标 + meta 事件（包含 request_id / trace_id / session_id）
            SSE_COUNTER.inc()
            meta = {"request_id": request_id_ctx.get(), "trace_id": trace_id_ctx.get(), "session_id": query.session_id}
            yield f"event: meta\ndata: {json.dumps(meta, ensure_ascii=False)}\n\n"

            response = openai_create_stream_with_retry(selected_model, history)

            ai_reply = ""
            # 使用 Server-Sent Events (SSE) 格式逐条推送：每个数据块以 `data:` 开头并以双换行结尾
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    ai_reply += content
                    yield f"data: {content}\n\n"

            # 5. 安全保存 AI 回复：流结束后使用上下文管理器创建独立会话，确保线程安全
            with SessionLocal() as db2:
                ai_msg = Message(session_id=query.session_id, role="assistant", content=ai_reply)
                db2.add(ai_msg)
                db2.commit()
                
            # 在保存后返回该条 assistant 消息的 id，方便客户端用 Last-Event-ID 恢复
            try:
                db2.refresh(ai_msg)
                yield f"id: {ai_msg.id}\n"
            except Exception:
                pass
            # 流结束通知
            yield "event: done\ndata: [DONE]\n\n"

        except Exception as e:
            OPENAI_ERRORS.inc()
            logging.exception("Streaming or generation failed")
            yield f"event: error\ndata: {str(e)}\n\n"

    resp = StreamingResponse(generate(), media_type="text/event-stream")
    # 明确在 SSE 建立时返回 request/trace id，便于客户端关联
    try:
        resp.headers["X-Request-ID"] = request_id_ctx.get() or ""
        resp.headers["X-Trace-ID"] = trace_id_ctx.get() or ""
    except Exception:
        pass
    return resp


@app.get("/chat_stream")
async def chat_stream_get(text: str = "", session_id: str = "", image: Optional[str] = None, last_event_id: Optional[str] = Header(None), db: Session = Depends(get_db)):
    # 请求指标
    try:
        REQUEST_COUNTER.labels(method="GET", endpoint="/chat_stream").inc()
        clean_text = text.strip()
        if not clean_text:
            clean_text = "请帮我分析和识别这张图片。"

        # 1. 捞取历史记录
        rows = db.query(Message).filter(Message.session_id == session_id).order_by(Message.id.asc()).all()

        now = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")

        has_valid_image = False
        if image and image.strip() and image.strip() != "string":
            has_valid_image = True

        if has_valid_image:
            system_prompt = f"当前系统时间：{now}。你现在是一个先进的多模态 AI 助手，能够同时理解文字和用户上传的图片。"
            selected_model = "glm-4v-flash"
        else:
            system_prompt = f"当前系统时间：{now}。你现在是一个先进的 AI 智能助手。"
            selected_model = "glm-4-flash"

        history = [{"role": "system", "content": system_prompt}]
        for row in rows:
            history.append({"role": row.role, "content": row.content})

        # 3. 智能组装请求体
        if has_valid_image:
            img_data = image.strip()
            if not img_data.startswith("data:image"):
                image_url = f"data:image/jpeg;base64,{img_data}"
            else:
                image_url = img_data

            current_user_content = [
                {"type": "text", "text": clean_text},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
            db_content = f"[上传了图片] {clean_text}"
        else:
            current_user_content = clean_text
            db_content = clean_text

        # 4. 存入用户消息
        user_msg = Message(session_id=session_id, role="user", content=db_content)
        db.add(user_msg)
        db.commit()
        try:
            db.refresh(user_msg)
        except Exception:
            pass

        history.append({"role": "user", "content": current_user_content})

    except Exception as e:
        db.rollback()
        logging.exception("聊天接口数据库操作失败")
        raise HTTPException(status_code=500, detail=str(e))

    def generate_sse():
        try:
            # 如果客户端提供了 Last-Event-ID，先把该 ID 之后的历史消息重放给客户端
            try:
                le = int(last_event_id) if last_event_id else None
            except Exception:
                le = None
            if le:
                with SessionLocal() as replay_db:
                    missing = replay_db.query(Message).filter(Message.session_id == session_id, Message.id > le).order_by(Message.id.asc()).all()
                    for m in missing:
                        payload = {"role": m.role, "content": m.content}
                        yield f"id: {m.id}\n"
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

            # SSE 指标 + meta 事件注入
            SSE_COUNTER.inc()
            meta = {"request_id": request_id_ctx.get(), "trace_id": trace_id_ctx.get(), "session_id": session_id}
            yield f"event: meta\ndata: {json.dumps(meta, ensure_ascii=False)}\n\n"

            response = openai_create_stream_with_retry(selected_model, history)

            ai_reply = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    ai_reply += content
                    yield f"data: {content}\n\n"

            with SessionLocal() as db2:
                ai_msg = Message(session_id=session_id, role="assistant", content=ai_reply)
                db2.add(ai_msg)
                db2.commit()
            try:
                db2.refresh(ai_msg)
                yield f"id: {ai_msg.id}\n"
            except Exception:
                pass
            yield "event: done\ndata: [DONE]\n\n"

        except Exception as e:
            OPENAI_ERRORS.inc()
            logging.exception("Streaming or generation failed")
            yield f"event: error\ndata: {str(e)}\n\n"

    resp = StreamingResponse(generate_sse(), media_type="text/event-stream")
    try:
        resp.headers["X-Request-ID"] = request_id_ctx.get() or ""
        resp.headers["X-Trace-ID"] = trace_id_ctx.get() or ""
    except Exception:
        pass
    return resp


def tail_file(path: str, lines: int = 500) -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            data = f.readlines()
            return ''.join(data[-lines:])
    except FileNotFoundError:
        return ''


@app.get("/logs/{level}")
async def get_logs(level: str, lines: int = 500):
    files = {'info': info_log, 'error': error_log, 'all': log_file}
    if level not in files:
        raise HTTPException(status_code=400, detail="invalid log level")
    content = tail_file(files[level], lines)
    return Response(content=content, media_type="text/plain; charset=utf-8")


@app.get("/metrics")
async def metrics_endpoint():
    try:
        data = generate_latest()
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)
    except Exception:
        logging.exception("metrics 采集失败")
        raise HTTPException(status_code=500, detail="metrics error")

if __name__ == "__main__":
    if not API_KEY:
        print("❌ 错误: 未找到 API_KEY")
    else:
        print(f"🚀 多模态 AI 后端服务已成功切换至安全端口：{PORT}，启动中...")
        uvicorn.run(app, host="127.0.0.1", port=PORT)