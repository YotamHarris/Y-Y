"""Boot a dynamically discovered iPhone simulator and prove the app executes frames."""
import json
import os
from pathlib import Path
import subprocess
import time

def run(*args):
    return subprocess.check_output(args, text=True).strip()

devices = json.loads(run('xcrun', 'simctl', 'list', 'devices', 'available', '-j'))['devices']
device = next(d for values in devices.values() for d in values if 'iPhone' in d['name'] and d.get('isAvailable'))
udid = device['udid']
if device['state'] != 'Booted':
    run('xcrun', 'simctl', 'boot', udid)
run('xcrun', 'simctl', 'bootstatus', udid, '-b')
run('xcrun', 'simctl', 'install', udid, 'build/ios-simulator/Release-iphonesimulator/TapDemo.app')
env = dict(os.environ, SIMCTL_CHILD_YY_SMOKE_FRAMES='120')
subprocess.run(['xcrun', 'simctl', 'launch', udid, 'com.yyengine.tapdemo'], env=env, check=True)
container = Path(run('xcrun', 'simctl', 'get_app_container', udid, 'com.yyengine.tapdemo', 'data'))
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    files = list(container.rglob('metrics.json'))
    if files:
        try:
            metrics = json.loads(files[0].read_text())
            if metrics.get('frames', 0) >= 120:
                Path('build/ios-simulator/metrics.json').write_text(json.dumps(metrics, indent=2))
                print(metrics)
                break
        except (ValueError, OSError):
            pass
    time.sleep(1)
else:
    raise SystemExit('Simulator app did not complete 120 frames')
