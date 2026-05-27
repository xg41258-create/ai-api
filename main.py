import os
import sqlite3
import uvicorn
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

# ================= 配置区域 =================
# 加载 .env 文件
load_dotenv()

DB_FILE = "chat_history.db"
# 从环境变量读取 API_KEY，如果没找到则为空
API_KEY = os.getenv("d8b534a4947d4af786788f11228be32a.JEeEu5J4AYvyoHyc")
BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
PORT = 8000
# ===========================================

app = FastAPI()

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化本地数据库
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# 初始化智谱 AI 客户端
client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)

class Query(BaseModel):
    text: str
    session_id: str

# 获取历史记录接口
@app.get("/history/{session_id}")
async def get_history(session_id: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r, "content": c} for r, c in rows]

# 聊天接口
@app.post("/chat")
async def chat_with_ai(query: Query):
    # 1. 捞取历史记录
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (query.session_id,))
    rows = cursor.fetchall()
    
    # 2. 注入当前日期（让 AI 有时间感）
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    history = [{"role": "system", "content": f"当前系统的真实时间是：{now}。如果用户询问日期或时间，请直接参考此信息回答。"}]
    
    for row in rows:
        history.append({"role": row[0], "content": row[1]})
    conn.close()

    # 3. 存入新消息
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (query.session_id, "user", query.text))
    conn.commit()
    conn.close()

    history.append({"role": "user", "content": query.text})

    def generate():
        try:
            response = client.chat.completions.create(
                model="glm-4-flash",
                messages=history,
                stream=True,
                tools=[{"type": "web_search", "web_search": {"enable": True}}]
            )
            
            ai_reply = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    ai_reply += content
                    yield content
            
            # 存入 AI 回复
            conn2 = sqlite3.connect(DB_FILE)
            cursor2 = conn2.cursor()
            cursor2.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", 
                            (query.session_id, "assistant", ai_reply))
            conn2.commit()
            conn2.close()
            
        except Exception as e:
            yield f"发生错误: {str(e)}"

    return StreamingResponse(generate(), media_type="text/event-stream")

if __name__ == "__main__":
    # 检查是否成功加载了 API Key
    if not API_KEY:
        print("❌ 错误: 未找到 API_KEY，请确保 .env 文件中配置了 OPENAI_API_KEY")
    else:
        print("🚀 AI 后端服务启动中...")
        print(f"🔗 接口访问地址: http://127.0.0.1:{PORT}")
        print("✅ 准备就绪，请运行你的前端页面！")
        uvicorn.run(app, host="127.0.0.1", port=PORT)