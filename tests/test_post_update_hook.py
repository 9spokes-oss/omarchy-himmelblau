"""hooks/post-update, the per-user omarchy update hook, against fake commands.

The fakes log every call. The companion's answers come from files in a state
directory, so each case sets up exactly what the hook will see.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

FAKES = {
    # sudo -n <companion> upgrade: the companion decides; a missing grant fails like sudo.
    'sudo': '''[ "$1" = -n ] || exit 99; shift
[ -e "$STATE/sudo-denied" ] && { echo "sudo: a password is required" >&2; exit 1; }
exec "$@"''',
    'companion': '''case "$1" in
--version) echo 9.9.9 ;;
upgrade) cat "$STATE/upgrade-output"; exit "$(cat "$STATE/upgrade-status")" ;;
check-plugin) [ -e "$STATE/installed-current" ] ;;
reboot-required) [ -e "$STATE/reboot" ] ;;
status) [ -e "$STATE/installed-current" ] || { echo "Fixed lock plugin 9.9.8 needs updating to 9.9.9"; exit 1; } ;;
esac''',
    # The hook must never update the plugin or restart the shell itself.
    'omarchy': 'exit 0',
    'git': 'exit 0',
    'omarchy-restart-shell': 'exit 0',
    'omarchy-state': 'exit 0',
    'notify-send': 'exit 0',
}


class PostUpdateHookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.state, self.bin, self.home = base / 'state', base / 'bin', base / 'home'
        for directory in (self.state, self.bin, self.home / '.config/omarchy/plugins/9spokes.himmelblau-lock'):
            directory.mkdir(parents=True)
        for name, body in FAKES.items():
            path = self.bin / name
            path.write_text(f'#!/bin/bash\necho "{name} $*" >> "$STATE/calls"\n{body}\n')
            path.chmod(0o755)
        self.hook = base / 'post-update'
        self.hook.write_text((ROOT / 'hooks/post-update').read_text()
                             .replace('companion=/usr/bin/omarchy-himmelblau', f'companion={self.bin}/companion'))
        self.upgrade('Already at companion 9.9.9; no changes', 0)

    def upgrade(self, output, status):
        (self.state / 'upgrade-output').write_text(output + '\n')
        (self.state / 'upgrade-status').write_text(str(status))

    def given(self, *names):
        for name in names:
            (self.state / name).touch()

    def run_hook(self, home=None):
        env = {'PATH': f'{self.bin}:/usr/bin:/bin', 'HOME': str(home or self.home), 'STATE': str(self.state)}
        result = subprocess.run(['bash', str(self.hook)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.state / 'calls').read_text().splitlines() if (self.state / 'calls').exists() else []
        return result.stdout, [call.split()[0] for call in calls], calls

    def test_not_set_up_does_nothing_more(self):
        self.upgrade('No root setup state; run setup first', 1)
        _, names, _ = self.run_hook()
        self.assertEqual(names, ['companion', 'sudo', 'companion'])

    def test_current_setup_and_plugin_change_nothing(self):
        self.given('installed-current')
        out, names, _ = self.run_hook()
        self.assertIn('Already at companion 9.9.9', out)
        for name in ('omarchy', 'git', 'omarchy-restart-shell', 'omarchy-state', 'notify-send'):
            self.assertNotIn(name, names)

    def test_an_older_plugin_is_reported_never_updated(self):
        self.upgrade('Root setup upgraded from companion 9.9.7 to 9.9.9.', 0)
        out, names, _ = self.run_hook()
        for name in ('omarchy', 'git', 'omarchy-restart-shell'):
            self.assertNotIn(name, names)
        self.assertIn('Update the lock plugin to 9.9.9 from an unlocked desktop: omarchy plugin update '
                      '9spokes.himmelblau-lock (review the diff)', out)
        self.assertEqual(out.count('Update the lock plugin'), 1)  # status isn't asked to repeat it.
        self.assertIn('notify-send', names)

    def test_a_symlinked_home_is_resolved_before_the_plugin_path(self):
        self.given('installed-current')
        alias = self.home.parent / 'home-alias'
        alias.symlink_to(self.home)
        out, names, calls = self.run_hook(home=alias)
        checked = [call for call in calls if call.startswith('companion check-plugin')]
        self.assertEqual(checked, [f'companion check-plugin {self.home.resolve()}/.config/omarchy/plugins/9spokes.himmelblau-lock'])
        self.assertNotIn('notify-send', names)

    def test_refusals_and_missing_sudo_are_reported_and_the_rest_still_runs(self):
        self.given('installed-current', 'reboot')
        self.upgrade('A graphical session or locker is running, or session state is unknown. This upgrade '
                     'replaces the lock PAM service: log out of every graphical session and run it from a '
                     'text console', 1)
        out, names, calls = self.run_hook()
        self.assertIn('Work sign-in upgrade did not run: A graphical session', out)
        self.assertIn('omarchy-state set reboot-required', calls)
        self.given('sudo-denied')
        (self.state / 'calls').unlink()
        out, names, _ = self.run_hook()
        self.assertIn('run sudo omarchy-himmelblau upgrade', out)
        self.assertIn('notify-send', names)

    def test_wrapper_runs_the_packaged_logic_or_nothing(self):
        wrapper = (ROOT / 'hooks/omarchy-himmelblau').read_text()
        self.assertIn('exec /usr/lib/omarchy-himmelblau/post-update', wrapper)
        if not Path('/usr/lib/omarchy-himmelblau/post-update').exists():
            result = subprocess.run(['bash', str(ROOT / 'hooks/omarchy-himmelblau')], capture_output=True)
            self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
