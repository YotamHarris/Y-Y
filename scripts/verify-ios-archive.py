"""Reject empty or generic Xcode archives before attempting IPA export."""
import argparse
from pathlib import Path
import plistlib
import re
import subprocess


def verify_device_apis(executable):
    symbols = subprocess.check_output(['xcrun', 'nm', '-u', str(executable)], text=True)
    libraries = subprocess.check_output(['xcrun', 'otool', '-L', str(executable)], text=True)
    if re.search(r'AVCapture\w*|CBCentralManager|CBPeripheralManager|CoreBluetooth\.framework', symbols + libraries):
        raise ValueError('Unexpected camera/Bluetooth APIs in iOS app. Disable unused SDL camera/HIDAPI/controller backends before upload.')
    print('Verified iOS binary excludes camera/Bluetooth APIs')


def verify(archive, bundle_id, check_device_apis=False):
    with (archive / 'Info.plist').open('rb') as handle:
        info = plistlib.load(handle)
    properties = info.get('ApplicationProperties', {})
    relative = properties.get('ApplicationPath', '')
    products = (archive / 'Products').resolve()
    app = (products / relative).resolve()
    if not relative or not app.is_relative_to(products / 'Applications') or app.suffix != '.app' or not app.is_dir():
        raise ValueError('Archive has no installed app. Check app SKIP_INSTALL=NO and INSTALL_PATH=$(LOCAL_APPS_DIR).')
    if sorted(path.name for path in products.iterdir()) != ['Applications'] or list((products / 'Applications').iterdir()) != [app]:
        raise ValueError('Archive contains extra installed products. Dependencies must use SKIP_INSTALL=YES.')
    with (app / 'Info.plist').open('rb') as handle:
        app_info = plistlib.load(handle)
    if app_info.get('CFBundleIdentifier') != bundle_id:
        raise ValueError('Archived app bundle identifier does not match the selected game')
    executable_name = app_info.get('CFBundleExecutable', '')
    executable = (app / executable_name).resolve()
    if not executable_name or not executable.is_relative_to(app) or not executable.is_file():
        raise ValueError('Archived app is missing its executable')
    if check_device_apis:
        verify_device_apis(executable)
    print('Verified app archive:', app.name, 'bundle:', bundle_id)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('bundle_id')
    parser.add_argument('--no-device-permissions', action='store_true', help='Verify that unused camera/Bluetooth backends are absent')
    args = parser.parse_args()
    try:
        verify(args.archive, args.bundle_id, args.no_device_permissions)
    except (ValueError, OSError, plistlib.InvalidFileException, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
