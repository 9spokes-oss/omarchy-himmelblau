#!/usr/bin/python3 -I
"""Generate an exact-host M365 extension policy; no browser or network access."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

EXTENSION = 'jlnfnnolkbjieggibinobhkjdfbpcohn'
INSTALLED_SCRIPT = Path('/usr/lib/omarchy-himmelblau/m365-sso-policy.py')
INSTALLED_PRESET = Path('/usr/share/omarchy-himmelblau/chromium-m365-sso.json')
PRESET = (INSTALLED_PRESET if Path(__file__).absolute() == INSTALLED_SCRIPT else
          Path(__file__).resolve().parents[1] / 'examples/chromium-m365-sso.json')
POLICY_DIR = Path('/etc/chromium/policies/managed')
POLICY_NAME = '90-omarchy-himmelblau-m365.json'
MISSING = object()
LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z', re.ASCII)


class PolicyError(Exception):
    pass


def hostname(value):
    if not value.isascii() or len(value) > 253:
        raise PolicyError('Expected an ASCII DNS hostname')
    value = value.lower()
    labels = value.split('.')
    if len(labels) < 2 or labels[-1].isdigit() or not all(LABEL.fullmatch(label) for label in labels):
        raise PolicyError('Expected an exact DNS hostname without URL, wildcard or port')
    return value


def tenant_host(value, onedrive=False):
    value = hostname(value)
    labels = value.split('.')
    if len(labels) != 3 or labels[1:] != ['sharepoint', 'com']:
        raise PolicyError('Tenant hosts must use the commercial .sharepoint.com domain')
    personal = labels[0].endswith('-my')
    if onedrive != personal or (onedrive and len(labels[0]) <= 3):
        raise PolicyError('Use a SharePoint tenant host and its OneDrive -my tenant host')
    return value


def apps_in(policy):
    try:
        return policy['3rdparty']['extensions'][EXTENSION]['wellKnownApps']
    except (KeyError, TypeError):
        return MISSING


def generate(sharepoint, onedrive, apps=()):
    sharepoint = tenant_host(sharepoint)
    onedrive = tenant_host(onedrive, onedrive=True)
    if sharepoint == onedrive:
        raise PolicyError('SharePoint and OneDrive hosts must be distinct')
    extras = [hostname(value) for value in apps]
    preset = apps_in(json.loads(PRESET.read_text(encoding='utf-8')))
    if not isinstance(preset, dict) or not preset or any(value is not True for value in preset.values()):
        raise PolicyError('The M365 preset is invalid')
    known = {}
    for host in preset:
        normalized = hostname(host)
        if normalized in known:
            raise PolicyError('The M365 preset contains duplicate normalized hosts')
        known[normalized] = True
    for host in [sharepoint, onedrive, *extras]:
        known[host] = True
    return {'3rdparty': {'extensions': {EXTENSION: {'wellKnownApps': known}}}}


def trusted(path, owner_uid, directory):
    metadata = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(metadata.st_mode) or metadata.st_uid != owner_uid or metadata.st_mode & 0o022:
        raise PolicyError('Policy paths must be trusted, nonsymlink and not writable by other users')
    if directory and metadata.st_mode & 0o005 != 0o005:
        raise PolicyError('Policy directories must be readable and traversable by Chromium')
    return metadata


def root_sources():
    """Never read checkout data or run a checkout generator with root privileges."""
    if Path(__file__).absolute() != INSTALLED_SCRIPT or PRESET != INSTALLED_PRESET:
        raise PolicyError('Root execution requires the installed generator and preset')
    if not sys.flags.isolated:
        raise PolicyError('Root execution requires /usr/bin/python3 -I')
    for source in (INSTALLED_SCRIPT, INSTALLED_PRESET):
        for directory in reversed(source.parents):
            trusted(directory, 0, True)
        trusted(source, 0, False)


def _install(policy, directory, boundary, owner_uid):
    """Internal paths are injectable for isolated tests, never CLI options."""
    boundary = Path(boundary)
    directory = Path(directory)
    relative = directory.relative_to(boundary)
    trusted(boundary, owner_uid, True)
    current = boundary
    missing = []
    for part in relative.parts:
        current /= part
        try:
            trusted(current, owner_uid, True)
        except FileNotFoundError:
            missing.append(current)
    target = directory / POLICY_NAME
    # Preflight all existing files before creating even a missing directory.
    if not missing:
        for candidate in directory.iterdir():
            if candidate.suffix.lower() != '.json':
                continue
            metadata = trusted(candidate, owner_uid, False)
            try:
                existing = json.loads(candidate.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError):
                raise PolicyError('An existing managed JSON policy cannot be parsed') from None
            if not isinstance(existing, dict):
                raise PolicyError('An existing managed JSON policy is not an object')
            if candidate == target:
                if stat.S_IMODE(metadata.st_mode) != 0o644 or existing != policy:
                    raise PolicyError('The target policy already exists with different content or permissions')
            elif apps_in(existing) is not MISSING and apps_in(existing) != apps_in(policy):
                raise PolicyError('Another managed policy has conflicting extension wellKnownApps')
        if target.exists():
            return False
    for parent in missing:
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        # Persist each new directory entry independently of the caller umask.
        descriptor = os.open(parent.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.m365-sso-', dir=directory)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            os.fchmod(output.fileno(), 0o644)
            output.write(json.dumps(policy, indent=2) + '\n')
            output.flush()
            os.fsync(output.fileno())
        # link is an exclusive publication: never replace a concurrently created target.
        os.link(temporary, target, follow_symlinks=False)
        os.unlink(temporary)
        temporary = None
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sharepoint-host', required=True, help='Actual tenant SharePoint hostname')
    parser.add_argument('--onedrive-host', required=True, help='Actual tenant OneDrive -my hostname')
    parser.add_argument('--app-host', action='append', default=[], help='Additional exact SaaS hostname (repeatable)')
    parser.add_argument('--install', action='store_true', help='Explicitly install the fixed Chromium managed policy as root')
    args = parser.parse_args(argv)
    try:
        if os.geteuid() == 0:
            root_sources()
        policy = generate(args.sharepoint_host, args.onedrive_host, args.app_host)
        if args.install:
            if os.geteuid() != 0:
                raise PolicyError('--install requires root')
            installed = _install(policy, POLICY_DIR, Path('/'), 0)
            print('M365 SSO policy installed.' if installed else 'M365 SSO policy already installed.')
        else:
            print(json.dumps(policy, indent=2))
    except (PolicyError, OSError, ValueError) as error:
        parser.exit(1, 'Policy generation refused: ' + str(error) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
