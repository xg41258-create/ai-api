import subprocess, sys
port = '8001'
try:
    out = subprocess.check_output(['netstat','-ano'], text=True, encoding='utf-8', errors='ignore')
except Exception as e:
    print('netstat-failed', e)
    sys.exit(2)
lines = out.splitlines()

pids = set()
for line in lines:
    if ':'+port in line:
        parts = [p for p in line.split() if p]
        if parts:
            pid = parts[-1]
            if pid.isdigit():
                pids.add(pid)

if not pids:
    print('No process on port', port)
    sys.exit(0)

for pid in pids:
    try:
        subprocess.check_call(['taskkill', '/PID', pid, '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('Killed', pid)
    except Exception as e:
        print('FailedKill', pid, e)
