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
if subprocess.check_output(['/usr/bin/git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip() != record['commit']:
    raise SystemExit('Use the exact release source checkout')
payload = (release / 'source.tar.gz').read_bytes()
if hashlib.sha256(payload).hexdigest() != record['source_sha256']:
    raise SystemExit('Release source checksum mismatch')
with tempfile.TemporaryDirectory() as temp:
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        archive.extractall(temp, filter='data')
    checkout = Path(temp) / 'omarchy-himmelblau'
    destination = Path(temp) / 'source'
    provenance = json.loads((checkout / 'packaging/sddm-himmelblau/provenance.json').read_text())
    name = 'sddm-' + provenance['baseline_tag'].removeprefix('v')
    subprocess.run([sys.executable, str(checkout / 'tools/prepare-native.py'), str(destination)], check=True)
    def normalise(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        info.mtime = 0
        info.mode = 0o755 if info.isdir() or info.mode & 0o111 else 0o644
        return info
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        archive.add(destination / name, arcname=name, filter=normalise)
    payload = gzip.compress(buffer.getvalue(), mtime=0)
    output = release / 'sddm-corresponding-source.tar.gz'
    output.write_bytes(payload)
    print(output.name, hashlib.sha256(payload).hexdigest())
