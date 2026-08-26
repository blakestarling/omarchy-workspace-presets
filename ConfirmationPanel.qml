import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "blakestarling.workspace-presets"
  ipcTarget: moduleName
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root
  readonly property var presetService: bar?.shell?.serviceFor(moduleName)
  readonly property var check: presetService ? presetService.pendingPreflight : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function open() {
    if (check) controller.show()
  }

  function closeWithoutCancel() {
    controller.hide()
  }

  function close() {
    controller.hide()
    if (presetService && presetService.pendingPreflight)
      presetService.cancelPreflight()
  }

  function confirm(policy) {
    if (!presetService || !presetService.pendingPreflight) return
    controller.hide()
    presetService.confirmLoad(policy)
  }

  onCheckChanged: if (!check && root.opened) root.closeWithoutCancel()

  component ActionButton: BorderSurface {
    id: action
    property string label: ""
    property bool primary: false
    signal clicked()

    implicitWidth: actionText.implicitWidth + Style.space(18)
    implicitHeight: Style.space(32)
    radius: Style.cornerRadius
    color: actionMouse.containsMouse
      ? Style.hoverFillFor(primary ? Color.urgent : root.foreground, Color.accent)
      : primary
        ? Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.14)
        : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.05)
    borderSpec: Border.controlSpec(
      actionMouse.containsMouse ? "hover-cursor" : "normal",
      primary ? Color.urgent : root.foreground,
      Color.accent
    )

    Text {
      id: actionText
      anchors.centerIn: parent
      text: action.label
      color: action.primary ? Color.urgent : root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
    }

    MouseArea {
      id: actionMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: action.clicked()
    }
  }

  KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: true
    focusTarget: keyCatcher
    contentWidth: popup.fittedContentWidth(Style.space(520))
    contentHeight: popup.fittedContentHeight(confirmationContent.implicitHeight, Style.space(560))

    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: true

      Shortcut {
        sequence: "Escape"
        context: Qt.WindowShortcut
        enabled: root.opened
        onActivated: root.close()
      }

      Keys.priority: Keys.BeforeItem
      Keys.onEscapePressed: function(event) {
        root.close()
        event.accepted = true
      }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: confirmationContent.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: confirmationContent
          x: 1
          width: Math.max(0, parent.width - 2)
          spacing: Style.space(12)

          Text {
            width: parent.width
            text: {
              if (!root.check) return "Confirm load"
              if (root.check.startupConfirmation === true)
                return "Launch ‘" + String(root.check.group.name || "startup group") + "’?"
              return root.check.kind === "group"
                ? "Replace these workspaces?" : "Replace this workspace?"
            }
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
            wrapMode: Text.Wrap
          }

          Text {
            width: parent.width
            text: {
              if (!root.check) return ""
              if (root.check.startupConfirmation === true)
                return "This group is configured to launch at startup. Continue for this session?"
              if (root.check.kind === "group")
                return "The group targets " + String((root.check.targets || []).length)
                  + " workspace(s)."
              return "The preset will replace workspace "
                + String(root.check.workspace ? root.check.workspace.name : "current") + "."
            }
            color: Color.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.Wrap
          }

          BorderSurface {
            width: parent.width
            implicitHeight: summaryColumn.implicitHeight + Style.space(20)
            radius: Style.cornerRadius
            color: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.09)
            borderSpec: Border.flat(Color.urgent, 1)

            Column {
              id: summaryColumn
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(10)
              spacing: Style.space(6)

              Text {
                width: parent.width
                text: {
                  if (!root.check) return ""
                  var count = root.check.kind === "group"
                    ? Number(root.check.windowCountToClose || 0)
                    : (root.check.windowsToClose || []).length
                  return count + " existing window(s) will receive normal close requests."
                }
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                font.bold: true
                wrapMode: Text.Wrap
              }

              Text {
                width: parent.width
                visible: root.check && root.check.kind !== "group"
                  && (root.check.conflicts || []).length > 0
                text: root.check
                  ? String((root.check.conflicts || []).length)
                    + " matching window(s) also exist on other workspaces."
                  : ""
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.Wrap
              }

              Text {
                width: parent.width
                text: "Applications that refuse to close are never force-killed."
                color: Color.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.Wrap
              }
            }
          }

          Row {
            spacing: Style.space(8)

            ActionButton {
              label: root.check && root.check.startupConfirmation === true
                ? "Skip this session" : "Cancel"
              onClicked: root.close()
            }
            ActionButton {
              label: root.check && root.check.startupConfirmation === true
                ? "Launch group" : "Launch new"
              primary: true
              onClicked: root.confirm("launch-new")
            }
            ActionButton {
              label: "Move existing"
              primary: true
              visible: root.check && root.check.kind !== "group"
                && (root.check.conflicts || []).length > 0
              onClicked: root.confirm("move-existing")
            }
          }
        }
      }
    }
  }
}
