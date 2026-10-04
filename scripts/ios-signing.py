"""Install ephemeral signing material without printing credentials to CI logs."""
import base64
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import urllib.request


def security(*args, sensitive=()):
    result = subprocess.run(['security', *args], capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip()
        # Never include argv: keychain and p12 passwords are command arguments.
        for value in (*sensitive, *(os.environ.get(key, '') for key in (
                'P12_PASSWORD', 'ASC_KEY_CONTENT', 'BUILD_CERTIFICATE_BASE64',
                'BUILD_PROVISION_PROFILE_BASE64', 'ASC_KEY_ID', 'ASC_ISSUER_ID'))):
            if value:
                detail = detail.replace(value, '[redacted]')
        raise SystemExit(f'Signing setup: security {args[0]} failed ({result.returncode}): {detail}')
    return result.stdout


def has_certificate(der, *keychains):
    result = subprocess.run(['security', 'find-certificate', '-a', '-p', *keychains],
                            capture_output=True, text=True)
    if result.returncode not in (0, 44):
        raise SystemExit('Signing setup: could not inspect installed certificates')
    for pem in re.findall(r'-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----',
                          result.stdout, re.S):
        if base64.b64decode(pem) == der:
            return True
    return False


def install_intermediate(keychain, temp):
    intermediate = temp / 'AppleWWDRCAG3.cer'
    with urllib.request.urlopen('https://www.apple.com/certificateauthority/AppleWWDRCAG3.cer', timeout=30) as response:
        der = response.read()
    intermediate.write_bytes(der)
    # Compare certificate content; the runner or a p12 may already contain WWDR.
    if has_certificate(der, str(keychain)) or has_certificate(der):
        print('Apple WWDR intermediate certificate already installed')
        return
    security('import', str(intermediate), '-t', 'cert', '-f', 'x509', '-k', str(keychain))


def main():
    temp = Path(os.environ['RUNNER_TEMP']) / 'yy-signing'
    keychain = temp / 'build.keychain-db'
    profile = Path.home() / 'Library/MobileDevice/Provisioning Profiles/yy.mobileprovision'
    if '--clean' in sys.argv:
        if keychain.exists():
            subprocess.run(['security', 'delete-keychain', str(keychain)], check=False, capture_output=True)
        profile.unlink(missing_ok=True)
        if temp.exists():
            import shutil
            shutil.rmtree(temp)
        return

    for key in ('BUILD_CERTIFICATE_BASE64', 'P12_PASSWORD', 'BUILD_PROVISION_PROFILE_BASE64',
                'YY_APPLE_TEAM_ID', 'YY_PROFILE_NAME', 'ASC_KEY_ID', 'ASC_ISSUER_ID', 'ASC_KEY_CONTENT'):
        if not os.environ.get(key):
            raise SystemExit(f'Missing GitHub testflight secret/variable: {key}')

    temp.mkdir(parents=True, exist_ok=True)
    certificate = temp / 'distribution.p12'
    certificate.write_bytes(base64.b64decode(os.environ['BUILD_CERTIFICATE_BASE64'], validate=True))
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_bytes(base64.b64decode(os.environ['BUILD_PROVISION_PROFILE_BASE64'], validate=True))
    password = secrets.token_urlsafe(24)
    original_keychains = shlex.split(security('list-keychains', '-d', 'user'))
    security('create-keychain', '-p', password, str(keychain), sensitive=(password,))
    security('set-keychain-settings', '-lut', '21600', str(keychain))
    security('unlock-keychain', '-p', password, str(keychain), sensitive=(password,))
    security('import', str(certificate), '-P', os.environ['P12_PASSWORD'], '-A', '-t', 'cert', '-f', 'pkcs12', '-k', str(keychain))
    install_intermediate(keychain, temp)
    security('set-key-partition-list', '-S', 'apple-tool:,apple:', '-k', password, str(keychain), sensitive=(password,))
    # Preserve access to Apple's chain in the runner's original keychains.
    security('list-keychains', '-d', 'user', '-s', str(keychain), *original_keychains)
    print('Ephemeral signing keychain installed')


if __name__ == '__main__':
    main()
