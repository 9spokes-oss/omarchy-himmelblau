import QtQuick
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "warpplugin" as Warp

// Offscreen harness (tests/warp-harness/run.sh): renders the WARP widget with Omarchy's own Commons/Ui and
// the theme under $HOME/.local/state/omarchy/current/theme, drives the real
// Panel/Service through fake-warp-cli, and saves PNGs to $WARP_SHOTS.
ShellRoot {
  id: shellRoot
  readonly property string shots: Quickshell.env("WARP_SHOTS")
  readonly property string stateFile: Quickshell.env("FAKE_WARP_STATE")
  readonly property string theme: Quickshell.env("WARP_THEME")
  readonly property var sequence: (Quickshell.env("WARP_SEQUENCE") || "connected").split(",")
  property int step: -1

  function setState(s) {
    Quickshell.execDetached(["sh", "-c", "printf '%s\\n' \"$1\" > \"$2\"", "sh", s, stateFile])
  }

  function snap(item, name) {
    item.grabToImage(function(result) {
      var ok = result.saveToFile(shots + "/" + theme + "-" + name + ".png")
      console.log("WARPHARNESS snap", name, ok)
    })
  }

  PluginBarApi {
    id: barApi
    pluginId: "9spokes.warp"
    moduleName: "9spokes.warp"
    foreground: Commons.Color.bar.text
    barForeground: Commons.Color.bar.text
    background: Commons.Color.bar.background
    urgent: Commons.Color.urgent
    fontFamily: Style.font.family
    barSize: Style.bar.sizeHorizontal
  }

  FloatingWindow {
    id: win
    implicitWidth: 520
    implicitHeight: 560
    color: Commons.Color.background

    Rectangle {
    id: canvas
    anchors.fill: parent
    color: Commons.Color.background
    Column {
      id: page
      x: 16; y: 16
      width: 488
      spacing: 14

      Text {
        text: "theme: " + shellRoot.theme
        color: Commons.Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }

      // Every icon state on the theme's bar background.
      Rectangle {
        id: strip
        width: parent.width
        height: Style.bar.sizeHorizontal + 22
        color: Commons.Color.bar.background
        radius: Style.cornerRadius
        Row {
          anchors.centerIn: parent
          spacing: 26
          Repeater {
            model: [
              { n: "connected", on: true, p: false, w: false },
              { n: "connecting", on: false, p: true, w: false },
              { n: "off", on: false, p: false, w: false },
              { n: "attention", on: false, p: false, w: true }
            ]
            Column {
              required property var modelData
              spacing: 3
              Warp.WarpIcon {
                anchors.horizontalCenter: parent.horizontalCenter
                iconSize: Style.bar.iconSize
                color: Commons.Color.bar.text
                badgeColor: Commons.Color.urgent
                on: modelData.on
                pending: modelData.p
                warning: modelData.w
              }
              Text {
                text: modelData.n
                color: Commons.Color.bar.text
                font.family: Style.font.family
                font.pixelSize: 9
              }
            }
          }
        }
      }

      // The real widget, as the bar would host it.
      Rectangle {
        width: parent.width
        height: Style.bar.sizeHorizontal + 10
        color: Commons.Color.bar.background
        Warp.Panel {
          id: widget
          anchors.right: parent.right
          anchors.rightMargin: 12
          anchors.verticalCenter: parent.verticalCenter
          bar: barApi
          settings: ({ warpCli: Quickshell.env("FAKE_WARP_CLI"), refreshIntervalSec: 5, listenForChanges: true })
        }
      }
    }
    }
  }

  // Per state: write it, reopen the panel (which re-polls), then snapshot.
  Timer {
    property int tick: 0
    interval: 1200
    repeat: true
    running: true
    onTriggered: {
      var phase = tick % 3
      var i = Math.floor(tick / 3)
      tick += 1
      if (i >= shellRoot.sequence.length) {
        running = false
        quitTimer.start()
        return
      }
      var name = shellRoot.sequence[i]
      if (phase === 0) { if (name.indexOf("keep") !== 0) shellRoot.setState(name) }
      else if (phase === 1) { if (name.indexOf("keep") !== 0) { widget.close(); widget.open() } }
      else {
        shellRoot.snap(canvas, name)
        if (i === 0) shellRoot.snap(strip, "strip")
        console.log("WARPHARNESS state", name, "windows")
      }
    }
  }

  Timer {
    id: quitTimer
    interval: 1500
    onTriggered: Qt.quit()
  }
}
