"""Prefetch the exact SDL archive used by CMake; validate every cache restore."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def pin(root=ROOT):
    cmake = (root / 'CMakeLists.txt').read_text()
    commit = re.search(r'set\(YY_SDL_COMMIT ([0-9a-f]{40})\)', cmake)
    digest = re.search(r'set\(YY_SDL_SHA256 ([0-9a-f]{64})\)', cmake)
    if not commit or not digest:
        raise ValueError('CMake must pin an SDL commit and archive SHA256')
    return commit[1], digest[1]


def checksum(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def prefetch(archive, commit, digest):
    if archive.exists() and checksum(archive) == digest:
        print('Reusing verified SDL archive:', archive)
        return
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_suffix('.download')
    try:
        request = urllib.request.Request(
            f'https://codeload.github.com/libsdl-org/SDL/tar.gz/{commit}',
            headers={'User-Agent': 'YYEngine-SDL-prefetch'})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        if checksum(temporary) != digest:
            raise ValueError('Downloaded SDL archive failed SHA256 verification')
        temporary.replace(archive)
        print('Prefetched verified SDL archive:', archive)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', action='store_true')
    args = parser.parse_args()
    commit, digest = pin()
    archive = ROOT / '.tools/sdl-cache' / f'{commit}.tar.gz'
    if args.metadata:
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
            output.write(f'sha256={digest}\n')
    else:
        prefetch(archive, commit, digest)
        with Path(os.environ['GITHUB_ENV']).open('a') as output:
            output.write(f'YY_SDL_ARCHIVE={archive.as_posix()}\n')


if __name__ == '__main__':
    main()
