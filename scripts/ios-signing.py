"""Install ephemeral signing material without printing credentials to CI logs."""
import base64
import os
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.request

temp = Path(os.environ['RUNNER_TEMP']) / 'yy-signing'
keychain = temp / 'build.keychain-db'
profile = Path.home() / 'Library/MobileDevice/Provisioning Profiles/yy.mobileprovision'
if '--clean' in sys.argv:
    if keychain.exists():
        subprocess.run(['security', 'delete-keychain', str(keychain)], check=False, stdout=subprocess.DEVNULL)
    profile.unlink(missing_ok=True)
    if temp.exists():
        import shutil
        shutil.rmtree(temp)
    sys.exit(0)

for key in ('BUILD_CERTIFICATE_BASE64', 'P12_PASSWORD', 'BUILD_PROVISION_PROFILE_BASE64', 'YY_APPLE_TEAM_ID', 'YY_PROFILE_NAME', 'ASC_KEY_ID', 'ASC_ISSUER_ID', 'ASC_KEY_CONTENT'):
    if not os.environ.get(key):
        raise SystemExit(f'Missing GitHub testflight secret/variable: {key}')
temp.mkdir(parents=True, exist_ok=True)
certificate = temp / 'distribution.p12'
certificate.write_bytes(base64.b64decode(os.environ['BUILD_CERTIFICATE_BASE64'], validate=True))
profile.parent.mkdir(parents=True, exist_ok=True)
profile.write_bytes(base64.b64decode(os.environ['BUILD_PROVISION_PROFILE_BASE64'], validate=True))
password = secrets.token_urlsafe(24)
def security(*args):
    subprocess.run(['security', *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
security('create-keychain', '-p', password, str(keychain))
security('set-keychain-settings', '-lut', '21600', str(keychain))
security('unlock-keychain', '-p', password, str(keychain))
security('import', str(certificate), '-P', os.environ['P12_PASSWORD'], '-A', '-t', 'cert', '-f', 'pkcs12', '-k', str(keychain))
intermediate = temp / 'AppleWWDRCAG3.cer'
with urllib.request.urlopen('https://www.apple.com/certificateauthority/AppleWWDRCAG3.cer', timeout=30) as response:
    intermediate.write_bytes(response.read())
security('import', str(intermediate), '-k', str(keychain))
security('set-key-partition-list', '-S', 'apple-tool:,apple:', '-k', password, str(keychain))
security('list-keychains', '-d', 'user', '-s', str(keychain))
print('Ephemeral signing keychain installed')
