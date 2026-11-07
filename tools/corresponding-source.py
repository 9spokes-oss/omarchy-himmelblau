#!/usr/bin/python3
"""Publish complete patched SDDM source alongside its companion build scripts.

Usage: corresponding-source.py RELEASE_DIRECTORY
The source is fetched and verified by prepare-native.py, never collected from a
private lab build tree. Publish this archive and source.tar.gz together.
"""
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

root = Path(__file__).resolve().parents[1]
release = Path(sys.argv[1]).resolve()
record = json.loads((release / 'release.json').read_text())
if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip() != record['commit']:
    raise SystemExit('Use the exact release source checkout')
if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain']):
    raise SystemExit('Corresponding-source export requires a clean checkout')
payload = (release / 'source.tar.gz').read_bytes()
if hashlib.sha256(payload).hexdigest() != record['source_sha256']:
    raise SystemExit('Release source checksum mismatch')
with tempfile.TemporaryDirectory() as temp:
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        archive.extractall(temp, filter='data')
    checkout = Path(temp) / 'omarchy-himmelblau'
    destination = Path(temp) / 'source'
    subprocess.run([sys.executable, str(checkout / 'tools/prepare-native.py'), str(destination)], check=True)
    source = destination / 'sddm-0.21.0'
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for path in sorted(source.rglob('*')):
            info = archive.gettarinfo(str(path), arcname='sddm-0.21.0/' + str(path.relative_to(source)))
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = 0
            if path.is_file():
                with path.open('rb') as data:
                    archive.addfile(info, data)
            else:
                archive.addfile(info)
    payload = gzip.compress(buffer.getvalue(), mtime=0)
    output = release / 'sddm-corresponding-source.tar.gz'
    output.write_bytes(payload)
    print(output.name, hashlib.sha256(payload).hexdigest())
