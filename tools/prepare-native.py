#!/usr/bin/python3
"""Fetch the checksum-pinned SDDM source and verify the complete patch result."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request

root = Path(__file__).resolve().parents[1]
recipe = root / 'packaging/sddm-himmelblau'
provenance = json.loads((recipe / 'provenance.json').read_text())
destination = Path(sys.argv[1]).resolve()
destination.mkdir(parents=True, exist_ok=False)
archive = destination / 'sddm.tar.gz'
# The reviewed archive uses the tag URL; its digest detects a moved tag.
url = 'https://codeload.github.com/sddm/sddm/tar.gz/refs/tags/' + provenance['baseline_tag']
with urllib.request.urlopen(url, timeout=60) as response:
    data = response.read(32 * 1024 * 1024)
if hashlib.sha256(data).hexdigest() != provenance['baseline_archive_sha256']:
    raise SystemExit('SDDM source archive checksum mismatch')
archive.write_bytes(data)
with tarfile.open(archive) as source:
    source.extractall(destination, filter='data')
tree = destination / ('sddm-' + provenance['baseline_tag'].removeprefix('v'))
for name, key in [('0000-arch-cmake4.patch', 'arch_patch_sha256'),
                  ('0001-native-conversation.patch', 'native_patch_sha256')]:
    patch = (recipe / name).read_bytes()
    if hashlib.sha256(patch).hexdigest() != provenance[key]:
        raise SystemExit('Patch checksum mismatch: ' + name)
    subprocess.run(['/usr/bin/patch', '--batch', '--fuzz=0', '-p1'], cwd=tree,
                   input=patch, check=True, timeout=30, stdout=subprocess.DEVNULL)
for name, expected in provenance['result_files'].items():
    if hashlib.sha256((tree / name).read_bytes()).hexdigest() != expected:
        raise SystemExit('Patched source checksum mismatch: ' + name)
if (tree / 'src/common/RecoveryAccount.h').read_bytes() != (root / 'src/recovery-account.h').read_bytes():
    raise SystemExit('Native and locker identity policies differ')
print(tree)
