import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// Cloudflare WARP bar widget for omarchy-shell.
//   left click   open the panel
//   right click  connect / disconnect
//   middle click refresh
// IPC: omarchy-shell 9spokes.warp {toggle|open|close|refresh|connect|disconnect|signIn|status}
Panel {
  id: root
  moduleName: "9spokes.warp"
  ipcTarget: "9spokes.warp"
  manageIpc: false

  property int rowIndex: -1
  readonly property color foreground: bar ? bar.foreground : Commons.Color.foreground
  readonly property color urgent: bar ? bar.urgent : Commons.Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool pending: warp.busy || warp.view.state === Model.STATES.connecting || warp.view.state === Model.STATES.disconnecting

  // Action rows shown under the status, in keyboard order.
  readonly property var actions: {
    var list = []
    if (warp.view.state === Model.STATES.unregistered) list.push({ id: "enroll", glyph: "󰍂", title: "Sign in to your organization", help: "Opens the Zero Trust sign-in page in your browser" })
    if (warp.view.state === Model.STATES.connected) list.push({ id: "reauth", glyph: "󰌆", title: "Re-authenticate Access", help: "Fresh Cloudflare Access sign-in for protected apps" })
    list.push({ id: "refresh", glyph: "󰑐", title: "Refresh", help: "Read the WARP status again" })
    return list
  }

  function runAction(id) {
    if (id === "enroll") warp.enroll()
    else if (id === "reauth") warp.reauthenticate()
    else if (id === "refresh") warp.refresh()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    rowIndex = -1
    warp.refresh()
    Qt.callLater(function() { if (panel.loadedContent) panel.loadedContent.forceActiveFocus() })
  }

  Service {
    id: warp
    settings: root.settings
  }

  ShellIpc {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { warp.refresh(); return "ok" }
    function connect(): string { warp.connect(); return "ok" }
    function disconnect(): string { warp.disconnect(); return "ok" }
    function signIn(): string { warp.enroll(); return "ok" }
    function status(): string { return warp.view.state + "\t" + warp.view.label + "\t" + warp.view.detail }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: warp.tooltip
    iconComponent: Component {
      Item {
        WarpIcon {
          x: (parent.width - width) / 2
          y: (parent.height - height) / 2
          iconSize: button.iconSize
          color: root.barForeground
          badgeColor: root.urgent
          on: warp.on
          pending: root.pending
          warning: warp.view.attention
        }
      }
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) warp.toggle()
      else if (buttonCode === Qt.MiddleButton) warp.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(340))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(420))

    content: PanelKeyCatcher {
      implicitHeight: column.implicitHeight
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (dy === 0) return
        // -1 is the hero's switch; 0.. are the action rows.
        var lowest = warp.canToggle ? -1 : 0
        if (root.rowIndex < lowest) root.rowIndex = lowest
        else root.rowIndex = Math.max(lowest, Math.min(root.actions.length - 1, root.rowIndex + dy))
      }
      onActivateRequested: {
        if (root.rowIndex === -1) warp.toggle()
        else if (root.rowIndex >= 0 && root.rowIndex < root.actions.length) root.runAction(root.actions[root.rowIndex].id)
      }
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") warp.refresh()
        else if (t === "t" || t === "T") warp.toggle()
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(12)

        PanelHero {
          id: hero
          width: parent.width
          title: "Cloudflare WARP"
          meta: warp.view.label + (warp.organization !== "" ? " · " + warp.organization : "")
          foreground: root.foreground
          fontFamily: root.fontFamily
          iconOpacity: warp.on ? 1.0 : 0.6
          iconComponent: Component {
            WarpIcon {
              iconSize: Style.font.display
              color: root.foreground
              badgeColor: root.urgent
              on: warp.on
              pending: root.pending
              warning: warp.view.attention
            }
          }
          trailingControl: Component {
            ToggleSwitch {
              id: powerSwitch
              visible: warp.canToggle
              checked: warp.on
              busy: warp.busy
              hasCursor: root.rowIndex === -1 && root.opened
              foreground: root.foreground
              onHovered: function(on) { if (on) root.rowIndex = -1 }
              onToggled: warp.toggle()

              PanelToolTip {
                visible: powerSwitch.containsMouse
                text: warp.on ? "Disconnect" : "Connect"
                fontFamily: root.fontFamily
              }
            }
          }
        }

        // Reason, policy refusal or action feedback. Plain text only: it can
        // carry daemon output.
        Text {
          readonly property string message: warp.lastError !== "" ? warp.lastError
            : warp.actionStatus !== "" ? warp.actionStatus
            : (warp.view.detail !== "" && warp.view.detail !== "Network healthy" ? warp.view.detail : "")
          visible: message !== ""
          width: parent.width
          text: message
          textFormat: Text.PlainText
          color: warp.lastError !== "" ? root.urgent : warp.actionStatus !== "" ? root.dim : warp.view.attention ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        Column {
          width: parent.width
          spacing: Style.spacing.labelGap
          visible: warp.daemonUp
          InfoPair { label: "Status"; value: warp.view.status !== "" ? warp.view.status : warp.view.label }
          InfoPair { label: "Organization"; value: warp.organization; visible: warp.organization !== "" }
          InfoPair { label: "Account"; value: warp.accountType; visible: warp.accountType !== "" }
        }

        PanelSeparator { foreground: root.foreground }

        Column {
          id: actionColumn
          width: parent.width
          spacing: Style.space(6)

          Repeater {
            model: root.actions
            ActionRow {
              required property var modelData
              required property int index
              width: actionColumn.width
              action: modelData
              rowIndex: index
            }
          }
        }
      }
    }
  }

  component ActionRow: CursorSurface {
    id: row
    property var action: ({})
    property int rowIndex: 0
    readonly property bool enabledRow: action.id === "refresh" || (warp.daemonUp && !warp.signingIn)

    hasCursor: root.opened && root.rowIndex === rowIndex
    foreground: root.foreground
    opacity: enabledRow ? 1.0 : 0.5
    implicitHeight: rowContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      enabled: row.enabledRow
      cursorShape: Qt.PointingHandCursor
      onEntered: root.rowIndex = row.rowIndex
      onClicked: root.runAction(row.action.id)
    }

    RowLayout {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      Text {
        text: row.action.glyph || ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        horizontalAlignment: Text.AlignHCenter
        Layout.preferredWidth: Style.font.icon * 1.4
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)
        Text {
          Layout.fillWidth: true
          text: row.action.title || ""
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }
        Text {
          Layout.fillWidth: true
          text: row.action.help || ""
          textFormat: Text.PlainText
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }
  }

  component InfoPair: Item {
    property string label: ""
    property string value: ""
    width: parent ? parent.width : 0
    implicitHeight: Math.max(labelText.implicitHeight, valueText.implicitHeight)

    Text {
      id: labelText
      anchors.left: parent.left
      text: parent.label
      textFormat: Text.PlainText
      color: root.foreground
      opacity: 0.6
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      id: valueText
      anchors.right: parent.right
      width: Math.max(0, parent.width - labelText.implicitWidth - Style.space(12))
      horizontalAlignment: Text.AlignRight
      text: parent.value
      textFormat: Text.PlainText
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }
  }
}
