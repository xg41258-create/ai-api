import http.client
import time
import sys

HOST = "127.0.0.1"
PORT = 8001

def run_once():
    conn = http.client.HTTPConnection(HOST, PORT, timeout=30)
    path = "/chat_stream?text=测试断连&session_id=simtest"
    print(f"Connecting to {HOST}:{PORT}{path}")
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        print("Status:", resp.status, resp.reason)
        start = time.time()
        while True:
            line = resp.readline()
            if not line:
                print("No more data from server.")
                break
            try:
                decoded = line.decode("utf-8").rstrip()
            except Exception:
                decoded = str(line)
            print(decoded)
            # 模拟在若干秒后强制断开以测试客户端断开场景
            if time.time() - start > 5:
                print("Simulating client-side disconnect (closing socket)")
                conn.close()
                break
    except Exception as e:
        print("Exception while streaming:", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass

if __name__ == '__main__':
    run_once()
