#include <QtTest>
#include <QProcess>
#include <QTimer>
#include "Auth.h"
using namespace SDDM;

class AuthLifecycleProofs : public QObject {
    Q_OBJECT
private slots:
    void deferredDeletionDoesNotNotifyClearedOwner() {
        QObject owner;
        Auth *current = new Auth(&owner);
        int clearedOwnerNotifications = 0;
        connect(current, &Auth::requestChanged, &owner, [&] {
            // Greeter::onHelperFinished clears its Auth pointer before deferred deletion.
            // Its requestChanged slot must never be invoked after that lifetime cutoff.
            if (!current) ++clearedOwnerNotifications;
        });
        QPointer<Auth> pending = current;
        current->deleteLater();
        current = nullptr;
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        QVERIFY(pending.isNull());
        QCOMPARE(clearedOwnerNotifications, 0);
    }
    void explicitStopStillNotifiesAndInvalidates() {
        Auth auth;
        QSignalSpy requests(&auth, &Auth::requestChanged);
        auth.stop();
        QVERIFY(auth.isCancelled());
        QCOMPARE(requests.count(), 1);
        QVERIFY(auth.request()->prompts().isEmpty());
    }
    void failedStartCancelDeadlineDoesNotKillRetry() {
        Auth auth;
        auto *child = auth.findChild<QProcess *>();
        auto *killTimer = auth.findChild<QTimer *>();
        QVERIFY(child);
        QVERIFY(killTimer);
        QCOMPARE(killTimer->interval(), 5000);
        // Shorten only the test clock; retain the real QProcess signals, Auth
        // FailedToStart handler and cancel timer connection to QProcess::kill.
        killTimer->setInterval(100);
        QSignalSpy errors(child, &QProcess::errorOccurred);
        QSignalSpy finished(&auth, &Auth::finished);
        child->start(QStringLiteral("/definitely/missing/omarchy-test-helper"), QStringList{});
        QCOMPARE(child->state(), QProcess::Starting);
        auth.cancel();
        QVERIFY(killTimer->isActive());
        QTRY_COMPARE(errors.count(), 1);
        QCOMPARE(child->error(), QProcess::FailedToStart);
        QCOMPARE(child->state(), QProcess::NotRunning);
        QCOMPARE(finished.count(), 1);
        QVERIFY(!killTimer->isActive());

        // Retry on the same real child before the previous cancel deadline.
        child->start(QStringLiteral("/bin/sleep"), {QStringLiteral("10")});
        QVERIFY(child->waitForStarted());
        QTest::qWait(300);
        QCOMPARE(child->state(), QProcess::Running);
        QCOMPARE(finished.count(), 1);
        child->terminate();
        QVERIFY(child->waitForFinished());
    }
    void cancelInvalidatesAtOnce() {
        Auth auth;
        QSignalSpy requests(&auth, &Auth::requestChanged);
        QSignalSpy finished(&auth, &Auth::finished);
        auth.cancel();
        QVERIFY(auth.isCancelled());
        QVERIFY(!auth.isActive());
        QCOMPARE(requests.count(), 1);
        QCOMPARE(finished.count(), 0); // no helper ran, so nothing finishes
    }
};
QTEST_GUILESS_MAIN(AuthLifecycleProofs)
#include "auth-lifecycle.moc"
