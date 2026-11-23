import QtQuick
import qs.Commons
import qs.Commons as Commons

// HARNESS STUB, replaces Ui/KeyboardPanel.qml in the offscreen harness copy
// only: the offscreen platform has no PanelWindow backend. Draws the same
// content inline, below the anchor, on the theme's popup colours.
Item {
  id: root
  required property Item anchorItem
  required property QtObject bar
  property var owner: null
  property int padding: Style.spacing.popupPadding
  property int contentWidth: Style.space(280)
  property int contentHeight: Style.space(200)
  property bool open: false
  property Item focusTarget: null
  property Component content: null
  readonly property Item loadedContent: loader.item
  readonly property real loadedContentHeight: loader.item ? loader.item.implicitHeight : 0

  function fittedContentWidth(width, cap) { return cap === undefined ? width : Math.min(width, cap) }
  function fittedContentHeight(implicitHeight, cap) { return Math.min(Math.max(0, implicitHeight), cap === undefined ? implicitHeight : cap) }
  function close() {}

  visible: open
  x: anchorItem ? anchorItem.x + anchorItem.width - width : 0
  y: anchorItem ? anchorItem.y + anchorItem.height + Style.gapsOut : 0
  width: contentWidth + padding * 2
  height: contentHeight + padding * 2
  z: 100

  Rectangle {
    anchors.fill: parent
    color: Commons.Color.popups.background
    border.color: Commons.Color.popups.border
    border.width: Math.max(1, Style.space(2))
    radius: Style.cornerRadius
  }

  Loader {
    id: loader
    x: root.padding
    y: root.padding
    width: root.contentWidth
    height: root.contentHeight
    active: true
    sourceComponent: root.content
  }
}
