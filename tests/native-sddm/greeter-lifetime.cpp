// Greeter lifetime after a successful login. Build-only: no PAM, VT or login.
//
// GreeterApp closes every view on LoginSucceeded. If that quits the greeter, its
// compositor tears down while the user session helper switches VTs, and the
// greeter session's VT cleanup can drop the pending switch: the session then
// stalls until waitForStarted() gives up after 30 s (sddm/sddm#2043, #2129).
// The daemon stops the greeter itself 5 s after the session has started.
#include <QGuiApplication>
#include <QTimer>
#include <QWindow>
#include <cstdio>

int main()
{
    qputenv("QT_QPA_PLATFORM", "offscreen");
    int argc = 1;
    char name[] = "sddm-greeter";
    char *argv[] = {name, nullptr};

#include "GreeterStartup.inc"

    QWindow view; // one view per screen; a single screen here
    view.show();
    QTimer::singleShot(0, &view, &QWindow::close); // GreeterApp: loginSucceeded -> close
    QTimer::singleShot(500, &app, [] { QCoreApplication::exit(42); }); // the daemon's stop
    const int status = app.exec();
    if (status != 42) {
        std::fprintf(stderr, "greeter quit by itself (status %d) when its last view closed\n", status);
        return 1;
    }
    std::puts("greeter keeps running after its views close on login");
    return 0;
}
