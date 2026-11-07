#!/usr/bin/python3 -I
"""Generate/check the distributable lock from pinned upstream fixtures + patches."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = '14abc0762ce38547d1912061969df58962f6f10a'
ORIGINAL = {
    'Service.qml': 'cf9da581ab749194d44d5f7f73dc028704373f59dd6b131a90b9c07419fed7c9',
    'LockView.qml': 'e5ec598e4dfafc0c6acb3ab8de1c3be810437ae943f6888fdcf1f92a6400876a',
    'FingerprintModel.js': '6684762de3e3b88445a165344a9093e8faedb0647137199f16ffd1861db6f87c',
    'poster.sh': '4941c8ae143a0ed0b226a6c54e1f94cbdd5937907cc01d0d0866c0cf34d916df',
    'lock-manifest.json': '58af8610a79045dfc09b541a5c2fdfd2e8886624549a49114ba424b4833cc546',
}


def generated():
    manifest = json.loads((ROOT / 'assets/manifest.json').read_text())
    output = {}
    for filename, expected in ORIGINAL.items():
        source = (ROOT / 'tests/fixtures' / filename).read_bytes()
        if hashlib.sha256(source).hexdigest() != expected:
            raise SystemExit('Upstream fixture hash mismatch')
        if filename == 'lock-manifest.json':
            continue
        if filename in ('Service.qml', 'LockView.qml'):
            key = 'lock-conversation' if filename == 'Service.qml' else 'lock-view'
            with tempfile.TemporaryDirectory() as work:
                target = Path(work) / filename
                target.write_bytes(source)
                subprocess.run(['/usr/bin/patch', '--batch', '--fuzz=0', '-p1', '-d', work],
                               input=(ROOT / 'assets' / (key + '.patch')).read_bytes(),
                               check=True, capture_output=True, timeout=5,
                               env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'})
                source = target.read_bytes()
            if hashlib.sha256(source).hexdigest() != manifest[key]['after']:
                raise SystemExit('Generated patched source hash mismatch')
        output['plugin/' + filename] = source
    output['plugin/LICENSE'] = (ROOT / 'LICENSE').read_bytes()
    provenance = {'upstream_repository':'https://github.com/omacom/omarchy', 'upstream_commit':UPSTREAM,
                  'upstream_path':'shell/plugins/lock', 'upstream_sha256':ORIGINAL,
                  'patches': {name: hashlib.sha256((ROOT / 'assets' / name).read_bytes()).hexdigest()
                              for name in ('lock-conversation.patch','lock-view.patch')},
                  'generated_sha256':{name:hashlib.sha256(data).hexdigest() for name,data in output.items()}}
    output['plugin/provenance.json'] = (json.dumps(provenance, indent=2) + '\n').encode()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    output = generated()
    for name, data in output.items():
        path = ROOT / name
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                raise SystemExit('Generated plugin drift; run plugin/generate.py explicitly')
        else:
            path.write_bytes(data)
            path.chmod(0o644 if path.suffix != '.sh' else 0o755)
    fixed_files = ['manifest.json', 'plugin/UPSTREAM-LICENSE', 'plugin/generate.py', *output]
    manifest_path = ROOT / 'assets/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    fixed = {'id':'9spokes.himmelblau-lock', 'version':'0.5.1',
             'upstream':{('manifest.json' if name == 'lock-manifest.json' else name): value for name,value in ORIGINAL.items()},
             'files':{name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in fixed_files}}
    if args.check:
        if manifest.get('fixed-plugin') != fixed:
            raise SystemExit('Companion fixed-plugin hash map differs')
    else:
        manifest['fixed-plugin'] = fixed
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
