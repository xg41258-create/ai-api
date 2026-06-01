import sys, time, urllib.request

url = 'http://127.0.0.1:8001/ui/eventsource_client.html'
for i in range(10):
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        print('STATUS', resp.getcode())
        data = resp.read(2000).decode('utf-8', errors='ignore')
        print('BODY_START')
        print(data[:1000])
        print('BODY_END')
        sys.exit(0)
    except Exception as e:
        print('WAIT', i+1, e)
        time.sleep(1)
print('FAILED')
