#!/usr/bin/env python3
"""Check that the native SDDM patch series still applies to Arch's sddm and upstream.

Replays Arch's sddm packaging (upstream tag plus Arch's cherry-picks), applies our
series on top and, with --build, compiles it and runs tests/native-sddm. It also
flags Arch packaging that differs from `arch_packaging_commit` in provenance.json,
upstream release tags newer than `baseline_tag`, and reports (without alerting)
whether the series applies to upstream develop.

Exit status: 0 all good, 1 alert (see the report), 2 the check itself failed.
With --notify STATUS it instead opens, comments on or closes the tracking issue
for a previous run's report, using `gh`.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'packaging/sddm-himmelblau'
UPSTREAM = 'https://github.com/sddm/sddm'
ARCH = 'https://gitlab.archlinux.org/archlinux/packaging/packages/sddm'
# Our series in PKGBUILD order, with the provenance key naming the upstream commit a
# patch backports: a tree that already has that commit gets the patch skipped.
SERIES = [('0000-arch-cmake4.patch', 'arch_cmake_commit'),
          ('0001-native-conversation.patch', None),
          ('0002-greeter-keep-running.patch', 'greeter_upstream_commit')]
LABEL = 'upstream-sddm'
TITLE = 'Native SDDM patches need attention against upstream'
FINGERPRINT = re.compile(r'<!-- upstream-sddm-fingerprint: (\w+) -->')
ARCH_SOURCE = 'git+https://github.com/$pkgname/$pkgname#tag=v$pkgver'
CHERRY_PICK = re.compile(r'git -C "?\$pkgname"? cherry-pick -n ([0-9a-f]{40})')
SECTION_LIMIT = 12000
BODY_LIMIT = 60000


def parse_pkgbuild(text):
    """What Arch's PKGBUILD does to the source, read with regexes (never sourced).

    Anything in source=() or prepare() other than the upstream tag and cherry-picks
    lands in `unknown`, because this check cannot replay it."""
    def value(key):
        found = re.search(rf'^{key}=(\S+)$', text, re.M)
        return found[1] if found else None
    source = re.search(r'^source=\((.*?)\)', text, re.M | re.S)
    sources = [s.strip('\'"') for s in source[1].split()] if source else []
    prepare = re.search(r'^prepare\(\)\s*\{\n(.*?)^\}', text, re.M | re.S)
    picks, unknown = [], [f'source: {s}' for s in sources if s != ARCH_SOURCE]
    for line in (prepare[1] if prepare else '').splitlines():
        line = re.sub(r'(^|\s+)#.*$', '', line).strip()
        if not line:
            continue
        pick = CHERRY_PICK.fullmatch(line)
        if pick:
            picks.append(pick[1])
        else:
            unknown.append(f'prepare: {line}')
    return {'pkgver': value('pkgver'), 'pkgrel': value('pkgrel'),
            'cherry_picks': picks, 'unknown': unknown}


def arch_drift(baseline, current):
    """Differences between the Arch packaging we built against and Arch's current one."""
    drift = [f'{key}: {baseline[key]} -> {current[key]}'
             for key in ('pkgver', 'pkgrel', 'cherry_picks') if baseline[key] != current[key]]
    return drift + [f'cannot replay {step}' for step in current['unknown']]


def version(tag):
    found = re.fullmatch(r'v(\d+(?:\.\d+)*)', tag)
    return tuple(map(int, found[1].split('.'))) if found else None


def newer_tags(tags, baseline_tag):
    """Release tags newer than the one we build, oldest first; ignores non-release tags."""
    base = version(baseline_tag)
    return sorted((t for t in tags if version(t) and version(t) > base), key=version)


def failed_hunks(output):
    """Map each file to the hunks GNU patch (LC_ALL=C) could not apply."""
    failures, current = {}, None
    for line in output.splitlines():
        if found := re.match(r"(?:patching|checking) file '?(.+?)'?$", line):
            current = found[1]
        elif found := re.match(r'Hunk #(\d+) FAILED', line):
            failures.setdefault(current, []).append('#' + found[1])
        elif line.startswith('Reversed (or previously applied) patch detected'):
            failures.setdefault(current, []).append('already applied')
        elif line.startswith("can't find file to patch"):
            failures.setdefault('(missing file)', []).append(line)
    return failures


def describe(name, failures, returncode):
    parts = [f"{file} {' '.join(hunks)}" for file, hunks in failures.items()]
    return f"{name}: " + ('; '.join(parts) if parts else f'patch exited {returncode}')


def fingerprint(alerts):
    return hashlib.sha256('\n'.join(sorted(alerts)).encode()).hexdigest()[:16]


def decide(status, issue, report_fingerprint):
    """What to do with the tracking issue: 'open', 'comment', 'close' or None.

    Comments only when the alerts changed, so an unchanged failure doesn't post daily.
    A broken check (status 2) neither opens an issue nor closes a real one."""
    if status == 1:
        if issue is None:
            return 'open'
        seen = {m for text in issue['texts'] for m in FINGERPRINT.findall(text)}
        return None if report_fingerprint in seen else 'comment'
    if status == 0 and issue is not None:
        return 'close'
    return None


def clip(text, limit=SECTION_LIMIT):
    return text if len(text) <= limit else '[... clipped ...]\n' + text[-limit:]


def run(args, cwd=None, check=True, stdin=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, input=stdin,
                            env={**os.environ, 'LC_ALL': 'C'})
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
    return result


def git(repo, *args):
    return run(['git', '-C', str(repo), *args]).stdout.strip()


class Tree:
    """One upstream ref with Arch's cherry-picks and then our series applied."""

    def __init__(self, label, repo, ref, picks, provenance, work):
        self.label, self.ref, self.patches, self.failure = label, ref, [], None
        self.path = work / re.sub(r'\W', '-', label)
        git(repo, 'worktree', 'add', '--detach', str(self.path), ref)
        self.commit = git(self.path, 'rev-parse', 'HEAD')
        for pick in picks:
            git(self.path, 'cherry-pick', '-n', pick)
        for name, key in SERIES:
            origin = provenance.get(key) if key else None
            if origin and (origin in picks or run(
                    ['git', 'merge-base', '--is-ancestor', origin, self.commit],
                    cwd=self.path, check=False).returncode == 0):
                self.patches.append((name, 'skipped: has upstream ' + origin[:12], ''))
                continue
            # The PKGBUILD's flags plus --forward: --batch alone would silently reverse
            # a patch that upstream already contains.
            result = run(['patch', '--batch', '--forward', '--fuzz=0', '-p1',
                          '-i', str(RECIPE / name)], cwd=self.path, check=False)
            if result.returncode == 0:
                self.patches.append((name, 'applied', ''))
                continue
            rejects = ''.join(f'--- {p.relative_to(self.path)}\n{p.read_text()}'
                              for p in sorted(self.path.rglob('*.rej')))
            self.failure = describe(name, failed_hunks(result.stdout), result.returncode)
            self.patches.append((name, 'FAILED', result.stdout + result.stderr + rejects))
            break

    def markdown(self):
        lines = ['| Patch | Result |', '| --- | --- |']
        lines += [f'| `{name}` | {state} |' for name, state, _ in self.patches]
        for name, _, detail in self.patches:
            if detail:
                lines += ['', f'<details><summary>{name}: patch output and rejected hunks</summary>',
                          '', '``````diff', clip(detail), '``````', '</details>']
        return '\n'.join(lines)


def build_and_prove(tree, work):
    """Compile like the native PKGBUILD and run the native proofs; return a failure or None."""
    jobs = str(os.cpu_count() or 2)
    steps = [('Qt6 configure', ['cmake', '-S', tree, '-B', work / 'qt6', '-DBUILD_WITH_QT6=ON',
                                '-DBUILD_MAN_PAGES=OFF']),
             ('Qt6 build', ['cmake', '--build', work / 'qt6', '--parallel', jobs]),
             ('Qt5 configure', ['cmake', '-S', tree, '-B', work / 'qt5', '-DBUILD_MAN_PAGES=OFF']),
             ('Qt5 greeter build', ['cmake', '--build', work / 'qt5/src/greeter', '--parallel', jobs]),
             ('Qt5 components build', ['cmake', '--build', work / 'qt5/components', '--parallel', jobs]),
             ('native proofs', [sys.executable, ROOT / 'tests/native-sddm/run.py', '--source', tree,
                                '--sddm-build', work / 'qt6', '--build-directory', work / 'proofs'])]
    for name, args in steps:
        result = run([str(a) for a in args], check=False)
        if result.returncode:
            return name, (result.stdout + result.stderr)
    return None


def check(args, work):
    provenance = json.loads((RECIPE / 'provenance.json').read_text())
    alerts, sections = [], []
    upstream, arch = work / 'upstream', work / 'arch'
    run(['git', 'clone', '--quiet', UPSTREAM, str(upstream)])
    run(['git', 'clone', '--quiet', ARCH + '.git', str(arch)])
    arch_head = git(arch, 'rev-parse', 'origin/HEAD')
    baseline = parse_pkgbuild(git(arch, 'show', provenance['arch_packaging_commit'] + ':PKGBUILD'))
    current = parse_pkgbuild(Path(args.arch_pkgbuild).read_text() if args.arch_pkgbuild
                             else git(arch, 'show', arch_head + ':PKGBUILD'))
    arch_version = f"{current['pkgver']}-{current['pkgrel']}"
    alerts += [f'Arch packaging changed: {d}' for d in arch_drift(baseline, current)]

    tag = 'v' + current['pkgver']
    tree = Tree('arch ' + arch_version, upstream, tag, current['cherry_picks'], provenance, work)
    if tag == provenance['baseline_tag'] and tree.commit != provenance['baseline_commit']:
        alerts.append(f"Upstream tag {tag} moved: {provenance['baseline_commit']} -> {tree.commit}")
    if tree.failure:
        alerts.append('Arch tree: ' + tree.failure)
    sections += [f'### Arch sddm {arch_version} (`{tag}` + Arch cherry-picks + our series)', '',
                 tree.markdown()]
    if args.build and not tree.failure:
        failed = build_and_prove(tree.path, work / 'build')
        if failed:
            alerts.append(f'Arch tree: {failed[0]} failed')
            sections += ['', f'<details><summary>{failed[0]} output</summary>', '', '``````',
                         clip(failed[1]), '``````', '</details>']
        else:
            sections += ['', 'Qt6 and Qt5 builds and the native proofs passed.']
    elif not args.build:
        sections += ['', 'Build and native proofs not run (no `--build`).']

    tags = git(upstream, 'tag', '--list', 'v*').split()
    for new in newer_tags(tags, provenance['baseline_tag']):
        release = Tree('release ' + new, upstream, new, [], provenance, work)
        alerts.append(f'New upstream release {new}: ' + (release.failure or 'our series applies'))
        sections += ['', f'### Upstream release `{new}`', '', release.markdown()]

    develop = Tree('develop', upstream, 'origin/develop', [], provenance, work)
    since = git(upstream, 'rev-list', '--count', f"{provenance['baseline_tag']}..origin/develop")
    sections += ['', '### Upstream develop (informational, never alerts)', '',
                 f"`{develop.commit}` is {since} commits past `{provenance['baseline_tag']}` "
                 f"([compare]({UPSTREAM}/compare/{provenance['baseline_tag']}...develop)). "
                 + ('Our series applies.' if not develop.failure else
                    f'Our series does not apply: {develop.failure}'), '', develop.markdown()]

    head = [f"## Upstream SDDM check: {'ALERT' if alerts else 'OK'}", '',
            f'- Arch packaging: {arch_version} at `{arch_head}` ({ARCH})'
            + (' with PKGBUILD from --arch-pkgbuild' if args.arch_pkgbuild else '') + '; '
            f"baseline `{provenance['arch_packaging_commit']}` "
            f"({baseline['pkgver']}-{baseline['pkgrel']})",
            f'- Upstream `{tag}`: `{tree.commit}`',
            f"- Upstream develop: `{develop.commit}`",
            f"- Newest upstream release tag: `{max(tags, key=lambda t: version(t) or ())}`"
            f" (we build `{provenance['baseline_tag']}`)", '']
    if alerts:
        head += ['### Alerts', ''] + [f'- {a}' for a in alerts] + [
            '', 'See "Upstream watch" in docs/sddm.md for what to do.', '']
    report = '\n'.join(head + sections)
    if len(report) > BODY_LIMIT:
        report = report[:BODY_LIMIT] + '\n\n[... report clipped ...]'
    if alerts:
        report += f'\n\n<!-- upstream-sddm-fingerprint: {fingerprint(alerts)} -->\n'
    return (1 if alerts else 0), report


def notify(status, report):
    """Apply decide() to the open tracking issue with `gh` (needs GH_TOKEN and GH_REPO)."""
    def gh(*args, body=None):
        return run(['gh', *args], stdin=body).stdout
    issues = json.loads(gh('issue', 'list', '--label', LABEL, '--state', 'open',
                           '--json', 'number,title,body,comments'))
    issue = next(({'number': str(i['number']), 'texts': [i['body']] + [c['body'] for c in i['comments']]}
                  for i in issues if i['title'] == TITLE), None)
    text = report.read_text() if report.exists() else ''
    found = FINGERPRINT.search(text)
    action = decide(status, issue, found[1] if found else None)
    env = os.environ
    link = (f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
            if 'GITHUB_RUN_ID' in env else 'a local run')
    if action == 'open':
        gh('label', 'create', LABEL, '--force', '--color', 'B60205',
           '--description', 'Native SDDM patches versus Arch and upstream')
        print(gh('issue', 'create', '--title', TITLE, '--label', LABEL, '--body-file', '-',
                 body=f'{text}\n\nFrom {link}.\n'))
    elif action == 'comment':
        gh('issue', 'comment', issue['number'], '--body-file', '-', body=f'{text}\n\nFrom {link}.\n')
    elif action == 'close':
        gh('issue', 'close', issue['number'], '--comment',
           f'The upstream SDDM check passes again ({link}).')
    print(f'tracking issue: {action or "no change"}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build', action='store_true', help='compile the Arch tree and run the native proofs')
    parser.add_argument('--report', type=Path, help='also write the Markdown report here')
    parser.add_argument('--arch-pkgbuild', help="use this file as Arch's current PKGBUILD (to test the check)")
    parser.add_argument('--notify', type=int, metavar='STATUS',
                        help="update the tracking issue for a previous run's --report and exit status")
    args = parser.parse_args()
    if args.notify is not None:
        return notify(args.notify, args.report)
    with tempfile.TemporaryDirectory(prefix='upstream-sddm-') as work:
        try:
            status, report = check(args, Path(work))
        except Exception as error:  # any failure of the check itself, never an alert
            detail = getattr(error, 'stderr', None) or traceback.format_exc()
            status, report = 2, f'## Upstream SDDM check: could not run\n\n``````\n{error}\n{clip(detail)}\n``````\n'
    print(report)
    if args.report:
        args.report.write_text(report)
    return status


if __name__ == '__main__':
    sys.exit(main())
