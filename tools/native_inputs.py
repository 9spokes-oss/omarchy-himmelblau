"""Decide whether a release rebuilds sddm-himmelblau or reuses the previous release's.

The companion pins the native package's exact file hashes, native builds are not
reproducible, and pacman does not replace an installed package of the same version.
So a release whose native inputs are unchanged must ship the previous release's
native package file, and a release that changes them must bump its pkgrel.
"""
import re
import subprocess

# Every repository path the native package's build, check() and package() read from
# source.tar.gz, plus the tool that produces its corresponding source.
NATIVE_INPUTS = ('packaging/sddm-himmelblau', 'tests/native-sddm', 'LICENSE',
                 'tools/prepare-native.py')
NATIVE_RECIPE = 'packaging/sddm-himmelblau/PKGBUILD'


class NativeInputsError(Exception):
    pass


def native_version(recipe):
    fields = [re.search(rf'^{key}=(\S+)$', recipe, re.M) for key in ('pkgver', 'pkgrel')]
    if not all(fields):
        raise NativeInputsError(f'{NATIVE_RECIPE} has no pkgver or pkgrel')
    return '-'.join(field[1] for field in fields)


def decide(root, commit):
    """Return the release.json fields describing the native package for commit."""
    def git(*args, check=True):
        result = subprocess.run(['/usr/bin/git', '-C', str(root), *args], capture_output=True)
        if check and result.returncode:
            raise NativeInputsError(result.stderr.decode().strip() or 'git ' + ' '.join(args) + ' failed')
        return result
    def tree(revision, path):
        # A path missing at a revision compares as missing.
        result = git('rev-parse', '--verify', '--quiet', f'{revision}:{path}', check=False)
        return result.stdout.decode().strip() or None
    version = native_version(git('show', f'{commit}:{NATIVE_RECIPE}').stdout.decode())
    fields = {'native_version': version, 'native_source_commit': commit}
    if git('rev-parse', '--verify', '--quiet', commit + '^', check=False).returncode:
        return fields
    described = git('describe', '--tags', '--abbrev=0', '--match', 'v[0-9]*', commit + '^', check=False)
    if described.returncode:
        return fields  # No earlier release: build the native package.
    tag = described.stdout.decode().strip()
    previous = git('rev-parse', f'refs/tags/{tag}^{{commit}}').stdout.decode().strip()
    if all(tree(previous, path) == tree(commit, path) for path in NATIVE_INPUTS):
        return {**fields, 'native_source_commit': previous, 'native_reused_from': tag}
    if native_version(git('show', f'{previous}:{NATIVE_RECIPE}').stdout.decode()) == version:
        raise NativeInputsError(f'Native inputs changed since {tag}, but sddm-himmelblau is still '
                                f'{version}; bump pkgrel in {NATIVE_RECIPE}')
    return fields
