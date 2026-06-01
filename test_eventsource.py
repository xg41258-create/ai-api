import http.client
import sys
import urllib.parse


def main():
    text = "测试 EventSource GET 流式响应"
    session_id = "ev-test-1"
    params = urllib.parse.urlencode({'text': text, 'session_id': session_id})
    conn = http.client.HTTPConnection('127.0.0.1', 8001, timeout=600)
    conn.request('GET', f'/chat_stream?{params}')
    resp = conn.getresponse()
    print('Status:', resp.status, resp.reason)
    if resp.status != 200:
        print(resp.read().decode())
        return
    try:
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
    except Exception as e:
        print('Error reading stream:', e)
    finally:
        conn.close()

if __name__ == '__main__':
    main()
