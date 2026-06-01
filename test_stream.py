import http.client
import json
import sys


def main():
    conn = http.client.HTTPConnection("127.0.0.1", 8001, timeout=600)
    payload = json.dumps({"text": "测试流式响应", "session_id": "test-session-1", "image": None}, ensure_ascii=False)
    headers = {"Content-Type": "application/json"}
    conn.request("POST", "/chat", body=payload.encode('utf-8'), headers=headers)
    resp = conn.getresponse()
    print(f"Status: {resp.status} {resp.reason}")
    if resp.status != 200:
        body = resp.read().decode(errors='ignore')
        print("Non-200 response body:\n", body)
        conn.close()
        return

    try:
        # 流式读取并原样输出
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
    except Exception as e:
        print("Exception while reading stream:", e)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
