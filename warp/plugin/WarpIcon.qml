import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// A cloud drawn from rounded rectangles in the theme's colours, so it follows
// the active Omarchy theme and avoids tiny-SVG rendering quirks in the bar.
// Not Cloudflare's logo: a generic cloud plus a state overlay.
//   on        solid cloud
//   off       faded cloud with a slash
//   pending   cloud pulses (connecting / disconnecting)
//   warning   urgent "!" badge (not enrolled, daemon down, unable)
Item {
  id: root

  property real iconSize: Style.font.icon
  property color color: Commons.Color.foreground
  property color badgeColor: Commons.Color.urgent
  property bool on: false
  property bool pending: false
  property bool warning: false

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  Item {
    id: cloud
    anchors.fill: parent
    opacity: root.on || root.pending ? 1.0 : 0.38

    Item {
      id: pulse
      anchors.fill: parent

      // Base, left puff, top puff: proportions of the icon box.
      Puff { x: 0; y: root.iconSize * 0.46; width: root.iconSize; height: root.iconSize * 0.40; radius: height / 2 }
      Puff { x: root.iconSize * 0.06; y: root.iconSize * 0.30; width: root.iconSize * 0.44; height: width; radius: width / 2 }
      Puff { x: root.iconSize * 0.30; y: root.iconSize * 0.12; width: root.iconSize * 0.58; height: width; radius: width / 2 }
    }

    SequentialAnimation {
      running: root.pending && !Style.reduceMotion
      loops: Animation.Infinite
      onRunningChanged: if (!running) pulse.opacity = 1.0
      NumberAnimation { target: pulse; property: "opacity"; to: 0.35; duration: 650; easing.type: Easing.InOutSine }
      NumberAnimation { target: pulse; property: "opacity"; to: 1.0; duration: 650; easing.type: Easing.InOutSine }
    }
  }

  Rectangle {
    visible: !root.on && !root.pending && !root.warning
    anchors.centerIn: parent
    width: parent.width * 1.18
    height: Math.max(2, parent.height * 0.12)
    radius: height / 2
    color: root.color
    rotation: -45
  }

  BorderSurface {
    visible: root.warning
    width: Math.max(7, parent.width * 0.46)
    height: width
    radius: width / 2
    color: root.badgeColor
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    anchors.rightMargin: -width * 0.12
    anchors.bottomMargin: -width * 0.12
    borderSpec: Border.flat(Commons.Color.popups.background, 1)

    Text {
      anchors.centerIn: parent
      text: "!"
      color: Commons.Color.background
      font.family: Style.font.family
      font.pixelSize: Math.max(6, parent.height * 0.72)
      font.bold: true
    }
  }

  component Puff: Rectangle {
    color: root.color
    antialiasing: true
  }
}
