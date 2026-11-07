#!/usr/bin/env python3
"""Extract production SDDM methods for the native proofs. Build-only: no PAM or login.

Each method is copied verbatim from the patched tree; only class names and
platform calls (NSS, logind, configuration, D-Bus) are renamed to fixtures.
"""
from pathlib import Path
import sys


def method(text, marker):
    start = text.index(marker)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        if text[end] == '{':
            depth += 1
        elif text[end] == '}':
            depth -= 1
        end += 1
    return text[start:end]


def methods(text, markers, *renames):
    body = '\n'.join(method(text, marker) for marker in markers)
    for old, new in renames:
        body = body.replace(old, new)
    return body + '\n'


source, target = map(Path, sys.argv[1:3])
target.mkdir(parents=True, exist_ok=True)

# The actual daemon socket parser; only power management and utilities are faked.
socket = (source / 'src/daemon/SocketServer.cpp').read_text()
for include in ['DaemonApp.h', 'PowerManager.h', 'Utils.h']:
    socket = socket.replace('#include "' + include + '"', '#include "FakePlatform.h"')
(target / 'SocketServer-fixture.cpp').write_text(socket)

helper = (source / 'src/helper/HelperApp.cpp').read_text()
(target / 'HelperMethods.inc').write_text(methods(
    helper, ['void HelperApp::doAuth()', 'QProcessEnvironment HelperApp::authenticated('],
    ('HelperApp::', 'HelperFixture::'), ('getuid()', 'fixtureHelperUid()')))

display = (source / 'src/daemon/Display.cpp').read_text()
(target / 'DisplayMethods.inc').write_text(methods(display, [
    'void Display::login(', 'void Display::beginConversation(', 'void Display::respondConversation(',
    'void Display::cancelConversation(', 'void Display::finishConversation(', 'void Display::clearPassPhrase(',
    'void Display::slotAuthenticationFinished(', 'void Display::slotHelperFinished(',
    'void Display::recordRecoveryResult(', 'void Display::slotRequestChanged()'],
    ('Display::', 'DisplayFixture::'), ('oh_read_recovery_account(', 'fixtureReadRecoveryAccount('),
    ('XorgDisplayServer', 'FixtureXorgDisplayServer'), ('OrgFreedesktopLogin1ManagerInterface', 'FixtureManager'),
    ('Logind::', 'FixtureLogind::'), ('QDBusConnection::systemBus()', '0'),
    ('mainConfig.', 'fixtureMainConfig.'), ('stateConfig.', 'fixtureStateConfig.')))

auth = (source / 'src/auth/Auth.cpp').read_text()
(target / 'AuthDispatch.inc').write_text(methods(
    auth, ['void Auth::Private::dataPending()', 'void Auth::Private::requestFinished()'],
    ('Auth::Private::', 'AuthDispatchFixture::'),
    ('Auth *auth = qobject_cast<Auth*>(parent());', 'FixtureAuthEvents *auth = events;')))

pam = (source / 'src/helper/backend/PamBackend.cpp').read_text()
(target / 'PamConversation.inc').write_text(methods(
    pam, ['int PamBackend::converse('], ('PamBackend::', 'PamConversationFixture::')))
(target / 'PamAuthenticate.inc').write_text(methods(
    pam, ['bool PamBackend::authenticate()'], ('PamBackend::', 'FixtureBackend::')))
(target / 'PamChangePassword.inc').write_text(methods(
    pam, ['bool PamBackend::changePassword()'],
    ('PamBackend::', 'FixtureBackend::'), ('oh_recovery_account(', 'fixtureRecoveryAccount(')))

handle = (source / 'src/helper/backend/PamHandle.cpp').read_text()
(target / 'PamAccountStrict.inc').write_text(methods(
    handle, ['bool PamHandle::acctMgmtStrict()'],
    ('PamHandle::', 'StrictAccountFixture::'), ('pam_acct_mgmt(', 'fixturePamAccount(')))
