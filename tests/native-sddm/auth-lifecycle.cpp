#include <QtTest>
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
