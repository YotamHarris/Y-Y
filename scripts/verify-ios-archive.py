"""Reject empty or generic Xcode archives before attempting IPA export."""
import argparse
from pathlib import Path
import plistlib


def verify(archive, bundle_id):
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
    print('Verified app archive:', app.name, 'bundle:', bundle_id)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('bundle_id')
    args = parser.parse_args()
    try:
        verify(args.archive, args.bundle_id)
    except (ValueError, OSError, plistlib.InvalidFileException) as error:
        raise SystemExit(str(error))
