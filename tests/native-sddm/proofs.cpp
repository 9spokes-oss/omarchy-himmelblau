// Native proofs for the SDDM conversation patch. The socket parsers and
// GreeterProxy are compiled unchanged; Display, Auth dispatch, helper and PAM
// methods are extracted verbatim by prepare.py and run against fixtures that
// fake only PAM, NSS, logind, configuration and process effects.
#include <QtTest>
#include <QDeadlineTimer>
#include <QLocalServer>
#include <QLocalSocket>
#include <QProcessEnvironment>
#include <QUuid>
#include <QPointer>
#include <functional>
#include <security/pam_appl.h>
#include <pwd.h>
#include <unistd.h>
#include "Messages.h"
#include "GreeterProxy.h"
#include "SocketServer.h"
#include "Session.h"
#include "AuthMessages.h"
#include "FakePlatform.h"
#include "RecoveryAccount.h"

// Recovery binding. The daemon only reads the file; the helper also checks NSS.
static bool fixtureIdentityAllowed = true;
static bool fixtureAnyName = false; // accept every name: only Display's own checks can refuse
static int fixtureIdentityReads = 0;
static uid_t fixtureIdentityUid = 1000;
static int fixtureReadRecoveryAccount(const char *user, struct oh_identity *identity) {
    ++fixtureIdentityReads;
    if (!fixtureIdentityAllowed || (!fixtureAnyName && strcmp(user, "requested@example.test")))
        return 0;
    strcpy(identity->upn, "requested@example.test");
    strcpy(identity->name, "canonical-fixture");
    identity->uid = fixtureIdentityUid;
    return 1;
}
static int fixtureRecoveryAccount(const char *user, uid_t, int, struct oh_identity *identity) {
    return fixtureReadRecoveryAccount(user, identity);
}

namespace SDDM {
FixtureDaemon fixtureDaemon;
FixtureDaemon *daemonApp = &fixtureDaemon;
}
using namespace SDDM;

static int fixtureAccountResult = PAM_SUCCESS, fixtureAccountCalls = 0;
static int fixturePamAccount(void *, int) {
    ++fixtureAccountCalls;
    return fixtureAccountResult;
}
struct StrictAccountFixture {
    void *m_handle = nullptr;
    int m_silent = 0, m_result = -1;
    bool acctMgmtStrict();
};
#include "PamAccountStrict.inc"

// Helper side: HelperApp::doAuth/authenticated and PamBackend methods.
namespace SDDM {
inline uid_t fixtureHelperUid() { return 0; } // models the root helper; never elevates the test

// Data still buffered after disconnectFromServer() stays readable, as with QLocalSocket.
struct FixtureSocket {
    int frames = 1, disconnects = 0;
    int bytesAvailable() const { return frames; }
    void disconnectFromServer() { ++disconnects; }
};

struct FixtureBackend;
struct FixturePamAuth {
    FixtureBackend *owner;
    bool authenticate();
    bool acctMgmt();
    bool acctMgmtStrict();
    bool chAuthTok(int);
    const void *getItem(int);
    QString errorString() const { return QStringLiteral("synthetic failure"); }
};
struct FixtureBackend {
    bool started = true, authenticated = true, accountAllowed = true, changeSucceeded = true;
    bool recovery = true, remap = false, policyDrift = false;
    int authCalls = 0, accountCalls = 0, changeCalls = 0, openCalls = 0;
    oh_identity m_recoveryIdentity{"requested@example.test", "canonical-fixture", 1000};
    FixturePamAuth pam{this};
    FixturePamAuth *m_pam = &pam;
    FixtureBackend *m_app = this;
    bool start(const QString &) { return started; }
    void error(const QString &, Auth::Error) {}
    bool authenticate();
    bool changePassword();
    bool pinRecovery() const { return recovery; }
    QString userName() { return QStringLiteral("canonical-fixture"); }
    bool openSession() { ++openCalls; return true; }
};
bool FixturePamAuth::authenticate() { ++owner->authCalls; return owner->authenticated; }
bool FixturePamAuth::acctMgmt() { ++owner->accountCalls; return owner->accountAllowed; }
bool FixturePamAuth::acctMgmtStrict() {
    if (owner->policyDrift)
        ++fixtureIdentityUid;
    return acctMgmt();
}
bool FixturePamAuth::chAuthTok(int) { ++owner->changeCalls; return owner->changeSucceeded; }
const void *FixturePamAuth::getItem(int) {
    return owner->remap && owner->accountCalls ? "other@example.test" : "requested@example.test";
}
#include "PamAuthenticate.inc"
#include "PamChangePassword.inc"

struct FixtureSession {
    QString path() const { return QStringLiteral("never-executed"); }
    QProcessEnvironment processEnvironment() const { return {}; }
    void setProcessEnvironment(const QProcessEnvironment &) {}
    qint64 processId() const { return 1; }
};

// What the fake socket stream reads and what it saw written.
struct FixtureWire {
    static inline Msg reply = AUTHENTICATED;
    static inline bool disconnected = false;
    static inline Msg sent = MSG_UNKNOWN;
    static inline int authAcks = 0, sessionAcks = 0, requestSends = 0;
    static inline Request serialized;
    static inline std::function<void()> duringSend;
    static void reset() {
        reply = AUTHENTICATED;
        disconnected = false;
        sent = MSG_UNKNOWN;
        authAcks = sessionAcks = requestSends = 0;
        serialized = Request();
        duringSend = {};
    }
};
struct SafeDataStream {
    FixtureSocket *socket;
    explicit SafeDataStream(FixtureSocket *value) : socket(value) {}
    SafeDataStream &operator<<(Msg value) { FixtureWire::sent = value; return *this; }
    SafeDataStream &operator<<(const Request &value) { FixtureWire::serialized = value; return *this; }
    template<class T> SafeDataStream &operator<<(const T &) { return *this; }
    SafeDataStream &operator>>(Msg &value) { value = FixtureWire::reply; return *this; }
    SafeDataStream &operator>>(QString &value) { value = QStringLiteral("canonical-fixture"); return *this; }
    SafeDataStream &operator>>(bool &value) { value = true; return *this; }
    template<class T> SafeDataStream &operator>>(T &) { return *this; }
    void send() {
        if (FixtureWire::sent == REQUEST) {
            ++FixtureWire::requestSends;
            if (FixtureWire::duringSend)
                FixtureWire::duringSend();
        }
        if (FixtureWire::sent == AUTHENTICATED)
            ++FixtureWire::authAcks;
        if (FixtureWire::sent == SESSION_STATUS)
            ++FixtureWire::sessionAcks;
    }
    void receive() { --socket->frames; }
    void reset() {}
    QDataStream::Status status() const { return FixtureWire::disconnected ? QDataStream::ReadPastEnd : QDataStream::Ok; }
};

class HelperFixture {
public:
    FixtureBackend backend;
    FixtureSession session;
    FixtureSocket socket;
    FixtureBackend *m_backend = &backend;
    FixtureSession *m_session = &session;
    FixtureSocket *m_socket = &socket;
    QString m_user = QStringLiteral("requested@example.test");
    qint64 m_id = 1;
    bool m_pinRecovery = false, m_sessionAuthorized = false;
    QByteArray m_cookie;
    int exitCode = -99;
    void exit(int code) { exitCode = code; }
    void utmpLogin(const QString &, const QString &, const QString &, qint64, bool) {}
    void sessionOpened(bool) {}
    void doAuth();
    QProcessEnvironment authenticated(const QString &);
};
#include "HelperMethods.inc"
}

// Daemon side: Display methods.
namespace SDDM {
static int fixtureUnlocks = 0, fixtureActivations = 0;
struct FixtureManager {
    FixtureManager(const QString &, const QString &, int) {}
    void UnlockSession(const QString &) { ++fixtureUnlocks; }
    void ActivateSession(const QString &) { ++fixtureActivations; }
};
struct FixtureLogind {
    static QString serviceName() { return {}; }
    static QString managerPath() { return {}; }
};
class FixtureXorgDisplayServer : public QObject {
    Q_OBJECT
public:
    QByteArray cookie() const { return {}; }
};
struct FixtureFlag { bool get() const { return false; } };
struct FixtureUsers { FixtureFlag RememberLastUser, RememberLastSession; };
static struct { FixtureUsers Users; } fixtureMainConfig;
struct FixtureSetting { void set(const QString &) {} void setDefault() {} };
static struct {
    struct { FixtureSetting User, Session; } Last;
    int saves = 0;
    void save() { ++saves; }
} fixtureStateConfig;

struct FixturePrompt {
    QByteArray response;
    bool isHidden = true;
    void setResponse(const QByteArray &value) { response = value; }
    bool hidden() const { return isHidden; }
    QString message() const { return QStringLiteral("fresh prompt"); }
};
struct FixtureRequest {
    FixturePrompt prompt;
    int completions = 0;
    QList<FixturePrompt *> prompts() { return {&prompt}; }
    void done() { ++completions; }
};
struct FixtureAuth {
    bool active = false, cancelled = false, recovery = false, autologinEnabled = false;
    int starts = 0, cancels = 0;
    FixtureRequest req;
    std::function<void()> onCancel;
    bool isActive() const { return active; }
    bool isCancelled() const { return cancelled; }
    bool pinRecovery() const { return recovery; }
    bool autologin() const { return autologinEnabled; }
    void setAutologin(bool value) { autologinEnabled = value; }
    void setGreeter(bool) {}
    void setSession(const QString &) {}
    void setDisplayServerCommand(const QString &) {}
    void setUser(const QString &) {}
    void setCookie(const QByteArray &) {}
    void setPinRecovery(bool value) { recovery = value; }
    void start() { ++starts; active = true; cancelled = false; }
    // Like Auth::cancel(): invalidates at once; the helper exits later.
    void cancel() { cancelled = true; ++cancels; if (onCancel) onCancel(); }
    FixtureRequest *request() { return &req; }
    QString user() const { return QStringLiteral("canonical"); }
};
struct FixtureSink {
    int finishes = 0, messages = 0;
    quint32 result = 99, prompt = 0;
    QString lastMessage;
    bool lastMessageIsError = false;
    void conversationFinished(QLocalSocket *, quint32, quint32 value) { ++finishes; result = value; }
    void conversationAuthorized(QLocalSocket *, quint32) {}
    void conversationPrompt(QLocalSocket *, quint32, quint32 value, const QString &, bool) { prompt = value; }
    void conversationMessage(QLocalSocket *, quint32, const QString &text, bool error) {
        ++messages;
        lastMessage = text;
        lastMessageIsError = error;
    }
};
class DisplayFixture : public QObject {
public:
    FixtureAuth auth;
    FixtureAuth *m_auth = &auth;
    FixtureSink sink;
    FixtureSink *m_socketServer = &sink;
    quint32 m_conversationId = 0, m_promptId = 0;
    bool m_waitingPrompt = false, m_sessionAuthorized = false, m_started = false, m_legacyPasswordPending = false;
    QPointer<QLocalSocket> m_conversationSocket, m_socket;
    QString m_passPhrase, m_reuseSessionId, m_sessionName;
    QObject *m_displayServer = nullptr;
    QHash<QLocalSocket *, quint32> m_seenAttempts;
    QDeadlineTimer m_recoveryNotBefore;
    qint64 m_recoveryDelay = 0;

    bool startAuthResult = false;
    int startAuthCalls = 0, loginSuccess = 0, loginFailure = 0, stops = 0, autologinFailures = 0;
    bool startAuth(const QString &, const QString &password, const Session &) {
        ++startAuthCalls;
        m_passPhrase = password;
        return startAuthResult;
    }
    void loginSucceeded(QLocalSocket *) { ++loginSuccess; }
    void loginFailed(QLocalSocket *) { ++loginFailure; }
    void stop() { ++stops; }
    void handleAutologinFailure() { ++autologinFailures; }

    // State after beginConversation() with a helper waiting on prompt 4 of attempt 2.
    void waitOnPrompt(QLocalSocket *socket) {
        m_conversationSocket = m_socket = socket;
        m_conversationId = 2;
        m_promptId = 4;
        m_waitingPrompt = true;
        auth.active = true;
    }
    // State after login() with the helper running.
    void legacyLogin(QLocalSocket *socket, const QString &password) {
        m_socket = socket;
        m_passPhrase = password;
        m_legacyPasswordPending = true;
        auth.active = true;
    }

    void login(QLocalSocket *, const QString &, const QString &, const Session &);
    void beginConversation(QLocalSocket *, quint32, const QString &, const Session &, bool);
    void respondConversation(QLocalSocket *, quint32, quint32, const QString &);
    void cancelConversation(QLocalSocket *, quint32);
    void finishConversation(quint32);
    void clearPassPhrase();
    void recordRecoveryResult(bool);
    void slotRequestChanged();
    void slotAuthenticationFinished(const QString &, bool);
    void slotHelperFinished(Auth::HelperExitStatus);
};
#include "DisplayMethods.inc"
}

// Auth::Private dispatch and the helper's PAM conversation.
namespace SDDM {
struct FixtureAuthEvents {
    int authentications = 0, sessions = 0;
    std::function<void()> duringAuthentication;
    void setUser(const QString &) {}
    void authentication(const QString &, bool) {
        ++authentications;
        if (duringAuthentication)
            duringAuthentication();
    }
    void sessionStarted(bool) { ++sessions; }
    void displayServerReady(const QString &) {}
    void error(const QString &, Auth::Error) {}
    void info(const QString &, Auth::Info) {}
};
struct FixtureAuthRequest {
    Request current;
    Request request() const { return current; }
    void setRequest(const Request *value = nullptr) { current = value ? *value : Request(); }
};
class AuthDispatchFixture {
public:
    using Error = Auth::Error;
    using Info = Auth::Info;
    static constexpr auto ERROR_NONE = Auth::ERROR_NONE, ERROR_INTERNAL = Auth::ERROR_INTERNAL;
    static constexpr auto INFO_NONE = Auth::INFO_NONE;
    FixtureSocket store;
    FixtureSocket *socket = &store;
    FixtureAuthEvents eventStore;
    FixtureAuthEvents *events = &eventStore;
    FixtureAuthRequest requestStore;
    FixtureAuthRequest *request = &requestStore;
    bool cancelled = false, pinRecovery = false, authResultSeen = false, authenticationSucceeded = false;
    QProcessEnvironment environment;
    QByteArray cookie;
    QString sessionPath, displayServerCmd;
    void dataPending();
    void requestFinished();
};
#include "AuthDispatch.inc"

struct FixturePamApp {
    QStringList infos, errors, prompts;
    QList<bool> hidden;
    QList<QByteArray> answers;
    void info(const QString &text, Auth::Info) { infos << text; }
    void error(const QString &text, Auth::Error) { errors << text; }
    Request request(const Request &sent) {
        prompts << sent.prompts[0].message;
        hidden << sent.prompts[0].hidden;
        if (answers.isEmpty())
            return {};
        Request reply(sent);
        reply.prompts[0].response = answers.takeFirst();
        return reply;
    }
};
class PamConversationFixture {
public:
    FixturePamApp app;
    FixturePamApp *m_app = &app;
    int converse(int, const pam_message **, pam_response **);
};
#include "PamConversation.inc"
}

static void freeResponses(pam_response *answers, int count) {
    for (int i = 0; i < count; ++i)
        free(answers[i].resp);
    free(answers);
}

static QByteArray frame(const std::function<void(QDataStream &)> &write) {
    QByteArray bytes;
    QDataStream out(&bytes, QIODevice::WriteOnly);
    write(out);
    return bytes;
}

class NativeProofs : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() { qRegisterMetaType<QLocalSocket *>(); }
    void init() {
        fixtureIdentityAllowed = true;
        fixtureAnyName = false;
        fixtureIdentityReads = 0;
        fixtureIdentityUid = 1000;
        fixtureUnlocks = fixtureActivations = fixtureStateConfig.saves = 0;
        FixtureWire::reset();
    }

    // Wire format: GreeterProxy and SocketServer.

    void greeterWaitsForCompleteFrames() {
        QLocalServer server;
        QVERIFY(server.listen("sddm-proxy-proof-" + QUuid::createUuid().toString(QUuid::WithoutBraces)));
        GreeterProxy proxy(server.fullServerName());
        QSignalSpy prompts(&proxy, &GreeterProxy::conversationPrompt);
        QTRY_VERIFY(server.hasPendingConnections());
        QLocalSocket *peer = server.nextPendingConnection();
        QTRY_VERIFY(peer->bytesAvailable() >= 4);
        peer->readAll();
        const QByteArray bytes = frame([](QDataStream &out) {
            out << quint32(DaemonMessages::Capabilities) << quint32(3)
                << quint32(DaemonMessages::HostName) << QStringLiteral("fragmented-host")
                << quint32(DaemonMessages::ConversationVersion) << quint32(1)
                << quint32(DaemonMessages::ConversationPrompt) << quint32(7) << quint32(4)
                << QStringLiteral("Number 42, then OTP:") << quint32(0);
        });
        for (int i = 0; i < bytes.size() - 1; ++i) {
            peer->write(bytes.mid(i, 1));
            peer->flush();
            QTest::qWait(1);
            QCOMPARE(prompts.count(), 0);
        }
        peer->write(bytes.right(1));
        peer->flush();
        QTRY_COMPARE(prompts.count(), 1);
        QCOMPARE(proxy.hostName(), QStringLiteral("fragmented-host"));
        QCOMPARE(proxy.conversationVersion(), quint32(1));
        QCOMPARE(prompts.at(0).at(0).toUInt(), quint32(7));
        QCOMPARE(prompts.at(0).at(1).toUInt(), quint32(4));
        QCOMPARE(prompts.at(0).at(3).toBool(), false);

        proxy.respondConversation(7, 4, QStringLiteral("synthetic-response"));
        QTRY_VERIFY(peer->bytesAvailable() > 0);
        QDataStream in(peer);
        quint32 opcode = 0, attempt = 0, prompt = 0;
        QString response;
        in >> opcode >> attempt >> prompt >> response;
        QCOMPARE(opcode, quint32(GreeterMessages::ConversationResponse));
        QCOMPARE(attempt, quint32(7));
        QCOMPARE(prompt, quint32(4));
        QCOMPARE(response, QStringLiteral("synthetic-response"));
        // Over the limit: never sent.
        proxy.respondConversation(7, 5, QString(ConversationResponseMaxChars + 1, QLatin1Char('x')));
        QTest::qWait(20);
        QCOMPARE(peer->bytesAvailable(), qint64(0));
    }

    void daemonWaitsForCompleteFrames() {
        SocketServer server;
        QVERIFY(server.start("proof"));
        QLocalSocket client;
        client.connectToServer(server.socketAddress());
        QVERIFY(client.waitForConnected());
        QSignalSpy responses(&server, &SocketServer::conversationResponse);
        QSignalSpy begin(&server, &SocketServer::conversationBegin);
        QSignalSpy cancel(&server, &SocketServer::conversationCancel);
        const QByteArray bytes = frame([](QDataStream &out) {
            out << quint32(GreeterMessages::BeginConversation) << quint32(9)
                << QStringLiteral("fixture@example.test") << Session() << quint32(1)
                << quint32(GreeterMessages::ConversationResponse) << quint32(9) << quint32(3) << QStringLiteral("fresh-only")
                << quint32(GreeterMessages::CancelConversation) << quint32(9);
        });
        for (int i = 0; i < bytes.size(); ++i) {
            client.write(bytes.mid(i, 1));
            client.flush();
            QTest::qWait(1);
        }
        QTRY_COMPARE(cancel.count(), 1);
        QCOMPARE(begin.count(), 1);
        QCOMPARE(begin.at(0).at(4).toBool(), true);
        QCOMPARE(responses.count(), 1);
        QCOMPARE(responses.at(0).at(1).toUInt(), quint32(9));
        QCOMPARE(responses.at(0).at(2).toUInt(), quint32(3));
        QCOMPARE(responses.at(0).at(3).toString(), QStringLiteral("fresh-only"));
    }

    void daemonDropsOversizeResponses() {
        SocketServer server;
        QVERIFY(server.start("proof-oversize"));
        QLocalSocket client;
        client.connectToServer(server.socketAddress());
        QVERIFY(client.waitForConnected());
        QSignalSpy responses(&server, &SocketServer::conversationResponse);
        client.write(frame([](QDataStream &out) {
            out << quint32(GreeterMessages::ConversationResponse) << quint32(9) << quint32(3)
                << QString(ConversationResponseMaxChars + 1, QLatin1Char('x'))
                << quint32(GreeterMessages::ConversationResponse) << quint32(9) << quint32(4)
                << QString(ConversationResponseMaxChars, QLatin1Char('y'));
        }));
        client.flush();
        QTRY_COMPARE(responses.count(), 1);
        QCOMPARE(responses.at(0).at(2).toUInt(), quint32(4));
        QCOMPARE(responses.at(0).at(3).toString().size(), ConversationResponseMaxChars);
        QCOMPARE(client.state(), QLocalSocket::ConnectedState);
    }

    void daemonDisconnectsOnUnknownOpcode() {
        SocketServer server;
        QVERIFY(server.start("proof-unknown"));
        QLocalSocket client;
        client.connectToServer(server.socketAddress());
        QVERIFY(client.waitForConnected());
        QSignalSpy cancel(&server, &SocketServer::conversationCancel);
        // The frame after the unknown opcode must not be parsed.
        client.write(frame([](QDataStream &out) {
            out << quint32(4242) << quint32(GreeterMessages::CancelConversation) << quint32(9);
        }));
        client.flush();
        QTRY_COMPARE(client.state(), QLocalSocket::UnconnectedState);
        QCOMPARE(cancel.count(), 0);
    }

    // Display: request validation and conversation state.

    void invalidUserNamesNeverStartHelpers() {
        fixtureAnyName = true; // only Display's validation can refuse
        QLocalSocket owner;
        const QStringList names = {QStringLiteral("--change-pin"), QStringLiteral("--autologin"), QStringLiteral("-x"),
                                   QString(), QString(QChar(0)), QString(257, QLatin1Char('a'))};
        for (const QString &user : names) {
            DisplayFixture legacy;
            legacy.startAuthResult = true;
            legacy.login(&owner, user, QStringLiteral("unused"), Session());
            QCOMPARE(legacy.startAuthCalls, 0);
            QCOMPARE(legacy.loginFailure, 1);
            for (bool recovery : {false, true}) {
                DisplayFixture native;
                native.beginConversation(&owner, 1, user, Session(), recovery);
                QCOMPARE(native.startAuthCalls, 0);
                QCOMPARE(native.auth.starts, 0);
                QCOMPARE(native.sink.result, quint32(ConversationFailed));
            }
        }
        QCOMPARE(fixtureIdentityReads, 0);
        DisplayFixture control;
        control.beginConversation(&owner, 1, QStringLiteral("anyone@example.test"), Session(), true);
        QCOMPARE(control.auth.starts, 1);
        QCOMPARE(fixtureIdentityReads, 1);
    }

    void recoveryUnresolvedIdentityNeverStartsHelper() {
        QLocalSocket owner;
        DisplayFixture display;
        fixtureIdentityAllowed = false;
        display.beginConversation(&owner, 1, QStringLiteral("requested@example.test"), Session(), true);
        QCOMPARE(fixtureIdentityReads, 1);
        QCOMPARE(display.auth.starts, 0);
        QCOMPARE(display.sink.result, quint32(ConversationFailed));
    }

    void staleDuplicateAndOversizeResponsesAreIgnored() {
        QLocalSocket owner, other;
        DisplayFixture display;
        display.waitOnPrompt(&owner);
        display.respondConversation(&owner, 1, 4, QStringLiteral("old-attempt"));
        display.respondConversation(&owner, 2, 3, QStringLiteral("old-prompt"));
        display.respondConversation(&other, 2, 4, QStringLiteral("other-socket"));
        display.respondConversation(&owner, 2, 4, QString(ConversationResponseMaxChars + 1, QLatin1Char('x')));
        QCOMPARE(display.auth.req.completions, 0);
        display.respondConversation(&owner, 2, 4, QStringLiteral("fresh"));
        display.respondConversation(&owner, 2, 4, QStringLiteral("duplicate"));
        QCOMPARE(display.auth.req.completions, 1);
        QCOMPARE(display.auth.req.prompt.response, QByteArray("fresh"));

        display.slotRequestChanged();
        QCOMPARE(display.sink.prompt, quint32(5));
        display.respondConversation(&owner, 2, 4, QStringLiteral("late"));
        QCOMPARE(display.auth.req.completions, 1);
        display.respondConversation(&owner, 2, 5, QStringLiteral("new-fresh"));
        QCOMPARE(display.auth.req.completions, 2);
        QCOMPARE(display.auth.req.prompt.response, QByteArray("new-fresh"));
    }

    void cancelledAttemptCannotAuthorize() {
        QLocalSocket owner;
        DisplayFixture display;
        display.waitOnPrompt(&owner);
        display.m_reuseSessionId = QStringLiteral("existing-session");
        // A success that arrives while cancelling must not unlock anything.
        display.auth.onCancel = [&] { display.slotAuthenticationFinished(QStringLiteral("canonical"), true); };
        display.cancelConversation(&owner, 2);
        QCOMPARE(display.auth.cancels, 1);
        QCOMPARE(display.loginSuccess, 0);
        QCOMPARE(fixtureUnlocks, 0);
        QCOMPARE(fixtureActivations, 0);
        QCOMPARE(fixtureStateConfig.saves, 0);
        QCOMPARE(display.sink.finishes, 0); // reported when the helper exits
        display.respondConversation(&owner, 2, 4, QStringLiteral("after-cancel"));
        QCOMPARE(display.auth.req.completions, 0);

        display.auth.active = false;
        display.slotHelperFinished(Auth::HELPER_SUCCESS);
        QCOMPARE(display.sink.result, quint32(ConversationCancelled));
        QCOMPARE(display.m_conversationId, quint32(0));
        QCOMPARE(display.stops, 0);
    }

    void conversationResultFollowsHelperStatus() {
        struct Case { bool recovery, authorized, cancelled; Auth::HelperExitStatus status; quint32 result; };
        const Case cases[] = {
            {true, false, false, Auth::HELPER_SUCCESS, ConversationPinChanged},
            {true, false, false, Auth::HELPER_AUTH_ERROR, ConversationFailed},
            {true, false, false, Auth::HELPER_OTHER_ERROR, ConversationFailed},
            {true, false, true, Auth::HELPER_SUCCESS, ConversationCancelled},
            {false, true, false, Auth::HELPER_SUCCESS, ConversationLogin},
            {false, false, false, Auth::HELPER_SUCCESS, ConversationFailed},
        };
        for (const Case &c : cases) {
            QLocalSocket owner;
            DisplayFixture display;
            display.waitOnPrompt(&owner);
            display.auth.recovery = c.recovery;
            display.auth.cancelled = c.cancelled;
            display.m_sessionAuthorized = c.authorized;
            display.auth.active = false;
            display.slotHelperFinished(c.status);
            QCOMPARE(display.sink.result, c.result);
            QCOMPARE(display.m_conversationId, quint32(0));
            if (c.recovery)
                QCOMPARE(display.stops, 0); // recovery never restarts the display
        }
    }

    void failedRecoveryIsThrottled() {
        QLocalSocket owner;
        DisplayFixture display;
        const QString user = QStringLiteral("requested@example.test");
        quint32 attempt = 0;
        int starts = 0;
        for (qint64 delay : {30000, 60000, 120000, 240000, 480000, 900000, 900000}) {
            display.beginConversation(&owner, ++attempt, user, Session(), true);
            QCOMPARE(display.auth.starts, ++starts);
            display.auth.active = false;
            display.slotHelperFinished(Auth::HELPER_AUTH_ERROR);
            QCOMPARE(display.m_recoveryDelay, delay);

            const int messages = display.sink.messages;
            display.beginConversation(&owner, ++attempt, user, Session(), true);
            QCOMPARE(display.auth.starts, starts);
            QCOMPARE(display.sink.result, quint32(ConversationFailed));
            QCOMPARE(display.sink.messages, messages + 1);
            QVERIFY(display.sink.lastMessageIsError);
            QCOMPARE(display.m_recoveryDelay, delay); // a refusal does not extend the wait
            display.m_recoveryNotBefore = QDeadlineTimer(0);
        }
        // Normal sign-in is not throttled.
        display.beginConversation(&owner, ++attempt, user, Session(), false);
        QCOMPARE(display.startAuthCalls, 1);
        display.m_conversationId = 0;

        // A cancelled reset counts as a failure; a completed one clears the backoff.
        display.beginConversation(&owner, ++attempt, user, Session(), true);
        display.cancelConversation(&owner, attempt);
        display.auth.active = false;
        display.slotHelperFinished(Auth::HELPER_OTHER_ERROR);
        QCOMPARE(display.sink.result, quint32(ConversationCancelled));
        QVERIFY(!display.m_recoveryNotBefore.hasExpired());
        display.m_recoveryNotBefore = QDeadlineTimer(0);
        display.beginConversation(&owner, ++attempt, user, Session(), true);
        display.auth.active = false;
        display.slotHelperFinished(Auth::HELPER_SUCCESS);
        QCOMPARE(display.sink.result, quint32(ConversationPinChanged));
        QCOMPARE(display.m_recoveryDelay, qint64(0));
        display.beginConversation(&owner, ++attempt, user, Session(), true);
        QCOMPARE(display.auth.starts, starts + 3);
    }

    // Display: legacy login() and autologin.

    void legacyPasswordAnswersOneHiddenPromptOnly() {
        QLocalSocket owner;
        DisplayFixture display;
        display.legacyLogin(&owner, QStringLiteral("first-only"));
        display.slotRequestChanged();
        QCOMPARE(display.auth.req.prompt.response, QByteArray("first-only"));
        QCOMPARE(display.auth.req.completions, 1);
        QVERIFY(display.m_passPhrase.isEmpty());
        QCOMPARE(display.auth.cancels, 0);

        // A second hidden prompt (retry, OTP) gets no answer, and the attempt fails.
        display.auth.req.prompt.response.clear();
        display.slotRequestChanged();
        QVERIFY(display.auth.req.prompt.response.isEmpty());
        QCOMPARE(display.auth.req.completions, 1);
        QCOMPARE(display.auth.cancels, 1);
        QCOMPARE(display.loginFailure, 1);

        // The stopped helper's exit does not restart the display and greeter,
        // and a late authentication result does not report a second failure.
        display.slotAuthenticationFinished(QStringLiteral("canonical"), false);
        display.auth.active = false;
        display.slotHelperFinished(Auth::HELPER_OTHER_ERROR);
        QCOMPARE(display.stops, 0);
        QCOMPARE(display.loginFailure, 1);
        DisplayFixture uncancelled;
        uncancelled.slotHelperFinished(Auth::HELPER_OTHER_ERROR);
        QCOMPARE(uncancelled.stops, 1);
    }

    void legacyLoginNeverAnswersVisiblePrompts() {
        QLocalSocket owner;
        DisplayFixture display;
        display.legacyLogin(&owner, QStringLiteral("secret"));
        display.auth.req.prompt.isHidden = false;
        display.slotRequestChanged();
        QVERIFY(display.auth.req.prompt.response.isEmpty());
        QCOMPARE(display.auth.req.completions, 0);
        QVERIFY(display.m_passPhrase.isEmpty());
        QCOMPARE(display.auth.cancels, 1);
        QCOMPARE(display.loginFailure, 1);
    }

    void unanswerableAutologinPromptStartsGreeter() {
        DisplayFixture display;
        display.auth.active = true;
        display.auth.autologinEnabled = true;
        display.slotRequestChanged();
        QCOMPARE(display.auth.req.completions, 0);
        QCOMPARE(display.auth.cancels, 1);
        QCOMPARE(display.autologinFailures, 1);
        QCOMPARE(display.loginFailure, 0);
    }

    void legacyLoginReportsStartFailure() {
        QLocalSocket owner;
        DisplayFixture display;
        display.login(&owner, QStringLiteral("requested@example.test"), QStringLiteral("secret"), Session());
        QCOMPARE(display.startAuthCalls, 1);
        QCOMPARE(display.loginFailure, 1);
        QVERIFY(display.m_passPhrase.isEmpty());
    }

    // Auth::Private dispatch.

    void authDispatcherGatesCancellationAndRoles() {
        auto deliver = [](AuthDispatchFixture &dispatcher, Msg opcode, int frames = 1) {
            FixtureWire::reply = opcode;
            FixtureWire::authAcks = FixtureWire::sessionAcks = 0;
            dispatcher.store.frames = frames;
            dispatcher.dataPending();
        };
        AuthDispatchFixture cancelled;
        cancelled.cancelled = true;
        deliver(cancelled, AUTHENTICATED);
        QCOMPARE(cancelled.events->authentications, 0);
        QCOMPARE(FixtureWire::authAcks, 0);
        QCOMPARE(cancelled.store.disconnects, 1);

        AuthDispatchFixture recovery;
        recovery.pinRecovery = true;
        deliver(recovery, AUTHENTICATED);
        QCOMPARE(recovery.events->authentications, 0);
        QCOMPARE(FixtureWire::authAcks, 0);

        AuthDispatchFixture recoverySession;
        recoverySession.pinRecovery = true;
        deliver(recoverySession, SESSION_STATUS);
        QCOMPARE(recoverySession.events->sessions, 0);
        QCOMPARE(FixtureWire::sessionAcks, 0);

        AuthDispatchFixture normal;
        deliver(normal, AUTHENTICATED);
        QCOMPARE(normal.events->authentications, 1);
        QCOMPARE(FixtureWire::authAcks, 1);
        QVERIFY(normal.authenticationSucceeded);

        AuthDispatchFixture cancelledDuringAck;
        cancelledDuringAck.events->duringAuthentication = [&] { cancelledDuringAck.cancelled = true; };
        deliver(cancelledDuringAck, AUTHENTICATED);
        QCOMPARE(cancelledDuringAck.events->authentications, 1);
        QCOMPARE(FixtureWire::authAcks, 0);
        QVERIFY(!cancelledDuringAck.authenticationSucceeded);
    }

    void rejectedFrameStopsDispatch() {
        // Frames still buffered after a rejection are never parsed.
        AuthDispatchFixture cancelled;
        cancelled.cancelled = true;
        FixtureWire::reply = AUTHENTICATED;
        cancelled.store.frames = 2;
        cancelled.dataPending();
        QCOMPARE(cancelled.store.frames, 1);
        QCOMPARE(cancelled.store.disconnects, 1);

        AuthDispatchFixture duplicate;
        duplicate.store.frames = 3;
        duplicate.dataPending();
        QCOMPARE(duplicate.events->authentications, 1);
        QCOMPARE(FixtureWire::authAcks, 1);
        QCOMPARE(duplicate.store.frames, 1);
        QCOMPARE(duplicate.store.disconnects, 1);
    }

    void requestFinishedPreservesReentrantNextRequest() {
        AuthDispatchFixture dispatcher;
        Request first, second;
        Prompt response;
        response.response = QByteArray("first-response");
        first.prompts.append(response);
        Prompt next;
        next.message = QStringLiteral("second prompt");
        second.prompts.append(next);
        dispatcher.request->setRequest(&first);
        FixtureWire::duringSend = [&] { dispatcher.request->setRequest(&second); };
        dispatcher.requestFinished();
        FixtureWire::duringSend = {};
        QCOMPARE(FixtureWire::requestSends, 1);
        QCOMPARE(FixtureWire::serialized.prompts.size(), 1);
        QCOMPARE(FixtureWire::serialized.prompts[0].response, QByteArray("first-response"));
        QCOMPARE(dispatcher.request->request().prompts.size(), 1);
        QCOMPARE(dispatcher.request->request().prompts[0].message, QStringLiteral("second prompt"));
        dispatcher.cancelled = true;
        dispatcher.requestFinished();
        QCOMPARE(FixtureWire::requestSends, 1);
        dispatcher.cancelled = false;
        dispatcher.socket = nullptr;
        dispatcher.requestFinished();
        QCOMPARE(FixtureWire::requestSends, 1);
    }

    // Helper: PAM conversation.

    void pamConversationUsesFreshResponseForEachPrompt() {
        PamConversationFixture backend;
        backend.app.answers = {QByteArray("first-pin"), QByteArray("visible-otp"), QByteArray("fresh-retry")};
        pam_message info{PAM_TEXT_INFO, "Use number 42"}, hidden{PAM_PROMPT_ECHO_OFF, "PIN:"},
            visible{PAM_PROMPT_ECHO_ON, "OTP:"}, error{PAM_ERROR_MSG, "Try again"};
        const pam_message *batch[] = {&info, &hidden, &visible, &error};
        pam_response *answers = nullptr;
        QCOMPARE(backend.converse(4, batch, &answers), PAM_SUCCESS);
        QCOMPARE(backend.app.prompts.size(), 2);
        QCOMPARE(backend.app.hidden, QList<bool>({true, false}));
        QCOMPARE(backend.app.infos, QStringList{QStringLiteral("Use number 42")});
        QCOMPARE(backend.app.errors, QStringList{QStringLiteral("Try again")});
        QVERIFY(!answers[0].resp);
        QCOMPARE(QByteArray(answers[1].resp), QByteArray("first-pin"));
        QCOMPARE(QByteArray(answers[2].resp), QByteArray("visible-otp"));
        QVERIFY(!answers[3].resp);
        freeResponses(answers, 4);

        const pam_message *retry[] = {&hidden};
        QCOMPARE(backend.converse(1, retry, &answers), PAM_SUCCESS);
        QCOMPARE(QByteArray(answers[0].resp), QByteArray("fresh-retry"));
        freeResponses(answers, 1);

        // Out of answers: the conversation fails rather than reuse one.
        QCOMPARE(backend.converse(1, retry, &answers), PAM_CONV_ERR);
        QVERIFY(!answers);
    }

    void pamConversationTruncatesLongTextAndRejectsLongResponses() {
        PamConversationFixture backend;
        const QByteArray longText(20000, 'n');
        pam_message info{PAM_TEXT_INFO, longText.constData()}, prompt{PAM_PROMPT_ECHO_OFF, longText.constData()};
        const pam_message *batch[] = {&info, &prompt};
        backend.app.answers = {QByteArray("ok")};
        pam_response *answers = nullptr;
        QCOMPARE(backend.converse(2, batch, &answers), PAM_SUCCESS);
        QCOMPARE(backend.app.infos.at(0).size(), 8192);
        QCOMPARE(backend.app.prompts.at(0).size(), 8192);
        freeResponses(answers, 2);

        const pam_message *single[] = {&prompt};
        backend.app.answers = {QByteArray(ConversationResponseMaxBytes + 1, 'r')};
        QCOMPARE(backend.converse(1, single, &answers), PAM_CONV_ERR);
        QVERIFY(!answers);
        backend.app.answers = {QByteArray(ConversationResponseMaxBytes, 'r')};
        QCOMPARE(backend.converse(1, single, &answers), PAM_SUCCESS);
        QCOMPARE(qstrlen(answers[0].resp), uint(ConversationResponseMaxBytes));
        freeResponses(answers, 1);
    }

    // Helper: session authorization and recovery.

    void missingOrWrongAckNeverOpensSession() {
        for (int variant = 0; variant < 3; ++variant) {
            FixtureWire::disconnected = variant == 0;
            FixtureWire::reply = variant == 1 ? SESSION_STATUS : AUTHENTICATED;
            HelperFixture helper;
            helper.doAuth();
            QCOMPARE(helper.backend.authCalls, 1);
            QCOMPARE(helper.backend.accountCalls, 1);
            QCOMPARE(helper.backend.openCalls, variant == 2 ? 1 : 0);
            if (variant != 2)
                QCOMPARE(helper.exitCode, int(Auth::HELPER_AUTH_ERROR));
        }
    }

    void accountDenialNeverOpensSession() {
        HelperFixture helper;
        helper.backend.accountAllowed = false;
        helper.doAuth();
        QCOMPARE(helper.backend.accountCalls, 1);
        QCOMPARE(helper.backend.openCalls, 0);
    }

    void strictAccountCheckHasNoTokenMutationSideEffects() {
        for (int code : {PAM_SUCCESS, PAM_NEW_AUTHTOK_REQD, PAM_IGNORE, PAM_USER_UNKNOWN, PAM_PERM_DENIED, PAM_SYSTEM_ERR}) {
            StrictAccountFixture account;
            fixtureAccountCalls = 0;
            fixtureAccountResult = code;
            QCOMPARE(account.acctMgmtStrict(), code == PAM_SUCCESS);
            QCOMPARE(fixtureAccountCalls, 1);
        }
    }

    void deniedRecoveryNeverChangesToken() {
        for (int variant = 0; variant < 4; ++variant) {
            init();
            HelperFixture helper;
            helper.m_pinRecovery = true;
            helper.backend.accountAllowed = variant != 0;
            helper.backend.remap = variant == 1;
            fixtureIdentityAllowed = variant != 2;
            helper.backend.policyDrift = variant == 3;
            helper.doAuth();
            QCOMPARE(helper.backend.changeCalls, 0);
            QCOMPARE(helper.backend.authCalls, 0);
            QCOMPARE(helper.backend.openCalls, 0);
            QCOMPARE(helper.exitCode, int(Auth::HELPER_AUTH_ERROR));
        }
    }

    void recoveryExitStatusIsTheResult() {
        for (bool changed : {true, false}) {
            HelperFixture helper;
            helper.m_pinRecovery = true;
            helper.backend.changeSucceeded = changed;
            helper.doAuth();
            QCOMPARE(helper.backend.changeCalls, 1);
            QCOMPARE(helper.backend.authCalls, 0);
            QCOMPARE(helper.backend.openCalls, 0);
            QCOMPARE(helper.exitCode, int(changed ? Auth::HELPER_SUCCESS : Auth::HELPER_AUTH_ERROR));
            QCOMPARE(FixtureWire::authAcks, 0);
        }
    }
};
QTEST_GUILESS_MAIN(NativeProofs)
#include "proofs.moc"
