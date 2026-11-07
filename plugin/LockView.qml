import QtQuick
import QtQuick.Effects
import qs.Commons
import qs.Commons as Commons
import qs.Ui

Item {
  id: root

  property string backgroundPath: ""
  property string videoPosterPath: ""
  property int backgroundVersion: 0
  property bool fingerprintConfigured: false
  property bool fingerprintUnavailable: false
  property bool authenticatingPassword: false
  property bool awaitingResponse: false
  property bool responseVisible: false
  property string conversationMessage: ""
  property string promptMessage: ""
  property string warningMessage: ""
  property bool conversationError: false
  property string failureMessage: ""
  property int failedAttempts: 0
  property bool inputEnabled: true
  property bool loadBackground: true
  property string passwordText: ""
  property bool syncingPasswordText: false

  readonly property string placeholderText: authenticatingPassword ? (awaitingResponse ? "Enter response" : Commons.I18n.t("lock.checking")) : "Press Enter to sign in"
  readonly property int fieldWidth: 381
  readonly property int fieldHeight: 67
  readonly property int outlineThickness: 3
  readonly property int fieldFontSize: Math.round(Style.font.heading * 1.125)
  readonly property int passwordDotFontSize: Math.round(Style.font.heading * 1.33)
  readonly property int passwordDotLetterSpacing: Math.round(Style.font.heading * 0.19)
  // Space to keep clear on each side of the field for the fingerprint icon
  // (icon width plus a gap) so the centered dots never run under it.
  readonly property real fingerprintReserve: fingerprintConfigured ? Math.round(fingerprintIcon.implicitWidth + 12) : 0
  // Shrink the dots to fit once the password outgrows the field, so every
  // keystroke stays visible — otherwise long passwords clip with no feedback.
  readonly property real passwordDotScale: dotMetrics.advanceWidth > 0
    ? Math.min(1, (passwordInput.width - 4) / dotMetrics.advanceWidth)
    : 1
  readonly property bool showPasswordCursor: inputEnabled && awaitingResponse && failureMessage.length === 0
  readonly property bool errorState: failureMessage.length > 0
  readonly property var inputBorderSpec: errorState
    ? Border.surfaceSpec("lock", "border-error", Commons.Color.lock.borderError, root.outlineThickness, "border-alpha")
    : Border.surfaceSpec("lock", "border-active", Commons.Color.lock.borderActive, root.outlineThickness, "border-alpha")

  // A video wallpaper shows its still poster: nothing plays behind the lock.
  readonly property bool video: Util.isVideoPath(root.backgroundPath)

  signal submitPassword(string password)
  signal passwordTextEdited(string password)
  signal clearFailureRequested()
  signal cancelRequested()
  signal recoveryRequested()
  signal wakeRequested()

  function forcePasswordFocus() {
    passwordInput.forceActiveFocus()
  }

  function clearPassword() {
    passwordTextEdited("")
  }

  // Waking a DPMS-blanked display can stall the compositor for seconds while
  // the monitor modesets, so the wake key's release arrives late and client-side
  // key repeat floods the field with that character. A held key has no business
  // typing a password; only holding Backspace/Delete to clear stays useful.
  function dropsAutoRepeat(key) {
    return key !== Qt.Key_Backspace && key !== Qt.Key_Delete
  }

  function syncPasswordText() {
    if (passwordInput.text === passwordText) return
    syncingPasswordText = true
    passwordInput.text = passwordText
    syncingPasswordText = false
  }

  // The field loses focus while a password is checked (it is disabled then),
  // and forcing focus onto it would not stick until the check ends.
  function restorePasswordFocus() {
    if (inputEnabled && passwordInput.enabled && !passwordInput.activeFocus) forcePasswordFocus()
  }

  onPasswordTextChanged: syncPasswordText()
  onAwaitingResponseChanged: Qt.callLater(forcePasswordFocus)
  onInputEnabledChanged: {
    if (inputEnabled) Qt.callLater(forcePasswordFocus)
  }

  // A VT switch, which the NVIDIA driver forces around every suspend, takes the
  // keyboard away and Qt drops the field's focus with it. Nothing gives it back
  // when the keyboard returns, so typing went nowhere until the field was clicked.
  Window.onActiveFocusItemChanged: Qt.callLater(restorePasswordFocus)
  Component.onCompleted: {
    syncPasswordText()
    if (inputEnabled) Qt.callLater(forcePasswordFocus)
  }

  // Measures the masked password at full size; passwordDotScale compares this
  // against the field width to decide how far the dots must shrink to fit.
  TextMetrics {
    id: dotMetrics
    font.family: Style.font.family
    font.pixelSize: root.passwordDotFontSize
    font.letterSpacing: root.passwordDotLetterSpacing
    text: "●".repeat(passwordInput.text.length)
  }

  Rectangle {
    anchors.fill: parent
    color: Commons.Color.background

    BackgroundMedia {
      id: wallpaper
      objectName: "lockWallpaper"
      anchors.fill: parent
      path: root.loadBackground ? (root.video ? root.videoPosterPath : root.backgroundPath) : ""
      version: root.backgroundVersion
      // Decode only once sized, at the lock's own size: an unsized first
      // request decoded the file at its native resolution, then again once
      // sized. That size is what the lock service keeps decoded ahead of the
      // lock, so the first frame has the wallpaper.
      cached: true
      constrainDecode: true
      decodeSize: Qt.size(width, height)
    }

    MultiEffect {
      anchors.fill: wallpaper
      source: wallpaper
      autoPaddingEnabled: false
      blurEnabled: root.loadBackground && wallpaper.ready
      blur: 1.0
      blurMax: 128
      blurMultiplier: 1.25
      contrast: -0.08
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      onClicked: { root.wakeRequested(); root.forcePasswordFocus() }
      onPositionChanged: root.wakeRequested()
    }

    BorderSurface {
      id: inputField
      width: root.fieldWidth
      height: root.fieldHeight
      anchors.centerIn: parent
      color: Commons.Color.lock.background
      borderSpec: root.inputBorderSpec
      radius: Style.cornerRadius
      clip: true

      TextInput {
        id: passwordInput
        anchors.fill: parent
        anchors.topMargin: inputField.borderTop
        // Reserve the fingerprint icon's width on both sides so the centered
        // dots stay symmetric and never slide under the icon as they grow.
        anchors.rightMargin: inputField.borderRight + 18 + root.fingerprintReserve
        anchors.bottomMargin: inputField.borderBottom
        anchors.leftMargin: inputField.borderLeft + 18 + root.fingerprintReserve
        verticalAlignment: TextInput.AlignVCenter
        horizontalAlignment: TextInput.AlignHCenter
        activeFocusOnPress: true
        clip: true
        enabled: root.inputEnabled
        readOnly: !root.awaitingResponse
        echoMode: root.responseVisible ? TextInput.Normal : TextInput.Password
        passwordCharacter: "\u25CF"
        passwordMaskDelay: 0
        color: Commons.Color.lock.text
        selectionColor: Commons.Color.lock.selection
        selectedTextColor: Commons.Color.lock.text
        font.family: Style.font.family
        font.pixelSize: text.length > 0 && !root.responseVisible ? Math.max(1, Math.floor(root.passwordDotFontSize * root.passwordDotScale)) : root.fieldFontSize
        font.letterSpacing: text.length > 0 && !root.responseVisible ? root.passwordDotLetterSpacing * root.passwordDotScale : 0
        cursorVisible: activeFocus && root.showPasswordCursor && text.length > 0
        cursorDelegate: Rectangle {
          width: 2
          color: Commons.Color.lock.text
          visible: passwordInput.cursorVisible
        }

        onTextChanged: {
          if (!root.syncingPasswordText) root.passwordTextEdited(text)
          if (text.length > 0) {
            root.wakeRequested()
          }
          if (text.length > 0 && root.failureMessage.length > 0) root.clearFailureRequested()
        }

        onAccepted: {
          var submitted = root.passwordText
          root.passwordTextEdited("")
          root.submitPassword(submitted)
        }

        Keys.onPressed: function(event) {
          root.wakeRequested()
          if (event.isAutoRepeat && root.dropsAutoRepeat(event.key)) {
            event.accepted = true
            return
          }
          if (event.modifiers & Qt.ControlModifier && event.key === Qt.Key_R) {
            root.passwordTextEdited("")
            root.recoveryRequested()
            event.accepted = true
          } else if (event.key === Qt.Key_Escape) {
            root.passwordTextEdited("")
            root.cancelRequested()
            event.accepted = true
          } else if (event.modifiers & Qt.ControlModifier && event.key === Qt.Key_U) {
            root.passwordTextEdited("")
            event.accepted = true
          }
        }
      }

      Text {
        textFormat: Text.PlainText
        anchors.fill: passwordInput
        text: root.failureMessage.length > 0 ? root.failureMessage : root.placeholderText
        visible: passwordInput.text.length === 0
        color: root.authenticatingPassword ? Commons.Color.lock.text : (root.failureMessage.length > 0 ? Commons.Color.lock.textError : Commons.Color.lock.placeholder)
        font.family: Style.font.family
        font.pixelSize: root.fieldFontSize
        font.italic: !root.authenticatingPassword && root.failureMessage.length > 0
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
      }

      // Fingerprint hint pinned inside the field's right edge when a sensor is
      // enrolled, so the user knows they can touch to unlock instead of typing.
      // Matches hyprlock, which draws its fingerprint icon in the same spot.
      // A reader the shell cannot reach crosses out rather than disappears, so
      // it stops inviting touches that can never unlock.
      Text {
        id: fingerprintIcon
        objectName: "fingerprintIndicator"
        textFormat: Text.PlainText
        anchors.right: parent.right
        anchors.rightMargin: inputField.borderRight + 18
        anchors.verticalCenter: parent.verticalCenter
        visible: root.fingerprintConfigured
        text: root.fingerprintUnavailable ? "󰺱" : "󰈷"
        color: root.fingerprintUnavailable ? Commons.Color.lock.textError : Commons.Color.lock.placeholder
        font.family: Style.font.family
        font.pixelSize: Math.round(root.fieldFontSize * 1.1)
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
      }
    }

    Flickable {
      id: promptPanel
      anchors.bottom: inputField.top
      anchors.bottomMargin: 20
      anchors.horizontalCenter: inputField.horizontalCenter
      width: Math.min(root.width - 48, 720)
      height: Math.min(promptText.implicitHeight, root.height * 0.25)
      contentHeight: promptText.height
      clip: true
      Text {
        id: promptText
        width: parent.width
        text: root.promptMessage
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Commons.Color.lock.text
        font.family: Style.font.family
        font.pixelSize: root.fieldFontSize
        horizontalAlignment: Text.AlignHCenter
      }
    }

    Flickable {
      anchors.bottom: promptPanel.top
      anchors.bottomMargin: 16
      anchors.horizontalCenter: inputField.horizontalCenter
      width: Math.min(root.width - 48, 720)
      height: Math.min(conversationText.implicitHeight, root.height * 0.35, Math.max(0, promptPanel.y - 32))
      contentHeight: conversationText.height
      clip: true
      visible: root.conversationMessage.length > 0
      Text {
        id: conversationText
        width: parent.width
        text: root.conversationMessage
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: root.conversationError ? Commons.Color.lock.textError : Commons.Color.lock.text
        font.family: Style.font.family
        font.pixelSize: root.fieldFontSize
        horizontalAlignment: Text.AlignHCenter
      }
    }

    Text {
      anchors.top: inputField.bottom
      anchors.topMargin: 20
      anchors.horizontalCenter: inputField.horizontalCenter
      text: "Cancel (Esc)"
      textFormat: Text.PlainText
      visible: root.authenticatingPassword
      color: Commons.Color.lock.text
      font.family: Style.font.family
      font.pixelSize: root.fieldFontSize
      MouseArea { anchors.fill: parent; onClicked: root.cancelRequested() }
    }

    Text {
      anchors.top: inputField.bottom
      anchors.topMargin: root.fingerprintConfigured && root.fingerprintUnavailable ? 64 : 20
      anchors.horizontalCenter: inputField.horizontalCenter
      text: "Reset work PIN (Ctrl+R)"
      textFormat: Text.PlainText
      visible: !root.authenticatingPassword && root.inputEnabled
      color: Commons.Color.lock.text
      font.family: Style.font.family
      font.pixelSize: root.fieldFontSize
      MouseArea { anchors.fill: parent; onClicked: root.recoveryRequested() }
    }

    // The crossed-out icon has no meaning to a user who has never seen it — it
    // is not intuitive that it signals a broken reader — so the words carry the
    // explanation and the icon only reinforces it.
    Text {
      objectName: "fingerprintUnavailableNotice"
      textFormat: Text.PlainText
      anchors.top: inputField.bottom
      anchors.topMargin: 18
      anchors.horizontalCenter: inputField.horizontalCenter
      visible: root.fingerprintConfigured && root.fingerprintUnavailable && !root.authenticatingPassword
      text: Commons.I18n.t("lock.fingerprintUnavailable")
      color: Commons.Color.lock.textError
      font.family: Style.font.family
      font.pixelSize: Style.font.heading
      font.italic: true
      horizontalAlignment: Text.AlignHCenter
    }

    Text {
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 48
      anchors.horizontalCenter: parent.horizontalCenter
      width: Math.min(root.width - 48, 720)
      visible: root.warningMessage.length > 0
      text: root.warningMessage
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      color: Commons.Color.lock.textError
      font.family: Style.font.family
      font.pixelSize: root.fieldFontSize
      horizontalAlignment: Text.AlignHCenter
    }
  }
}
