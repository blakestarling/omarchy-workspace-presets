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
  property bool openedFromHotkey: false
  property string editingId: ""
  property string editingName: ""
  property string confirmAction: ""
  property var confirmPreset: null
  readonly property var barIdentity: hostWidget || root
  readonly property var presetService: bar?.shell?.serviceFor(moduleName)
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function open() {
    openedFromHotkey = false
    controller.show()
    if (presetService) presetService.refresh()
  }

  function openFromHotkey() {
    openedFromHotkey = true
    controller.show()
    if (presetService) presetService.refresh()
  }

  function close() {
    editingId = ""
    confirmAction = ""
    confirmPreset = null
    controller.hide()
  }

  function toggle() { opened ? close() : openFromHotkey() }

  component ActionButton: BorderSurface {
    id: action
    property string label: ""
    property bool destructive: false
    property color foreground: Color.foreground
    property string fontFamily: Style.font.family
    signal clicked()

    implicitWidth: actionText.implicitWidth + Style.space(18)
    implicitHeight: Style.space(30)
    radius: Style.cornerRadius
    color: actionMouse.containsMouse
      ? Style.hoverFillFor(destructive ? Color.urgent : foreground, Color.accent)
      : Qt.rgba(foreground.r, foreground.g, foreground.b, 0.05)
    borderSpec: Border.controlSpec(
      actionMouse.containsMouse ? "hover-cursor" : "normal",
      destructive ? Color.urgent : foreground,
      Color.accent
    )
    opacity: enabled ? 1 : 0.4

    Text {
      id: actionText
      anchors.centerIn: parent
      text: action.label
      color: action.destructive ? Color.urgent : action.foreground
      font.family: action.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
    }

    MouseArea {
      id: actionMouse
      anchors.fill: parent
      enabled: action.enabled
      hoverEnabled: true
      cursorShape: action.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
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
    contentWidth: popup.fittedContentWidth(Style.space(620))
    contentHeight: popup.fittedContentHeight(Style.space(680))

    Flickable {
      id: scroll
      anchors.fill: parent
      contentWidth: width
      contentHeight: content.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height

      Column {
        id: content
        width: scroll.width
        spacing: Style.space(12)

        Row {
          width: parent.width
          spacing: Style.space(10)

          Column {
            width: parent.width - refreshButton.width - parent.spacing
            spacing: Style.space(2)

            Text {
              text: "Workspace Presets"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Text {
              width: parent.width
              text: "Cold-launch applications and rebuild the current workspace"
              color: Color.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              elide: Text.ElideRight
            }
          }

          ActionButton {
            id: refreshButton
            label: "Refresh"
            foreground: root.foreground
            fontFamily: root.fontFamily
            enabled: root.presetService && !root.presetService.busy
            onClicked: root.presetService.refresh()
          }
        }

        BorderSurface {
          width: parent.width
          implicitHeight: saveColumn.implicitHeight + Style.space(20)
          radius: Style.cornerRadius
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.04)
          borderSpec: Border.flat(Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14), 1)

          Column {
            id: saveColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(10)
            spacing: Style.space(8)

            Text {
              text: "Save current workspace"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }
            Row {
              width: parent.width
              spacing: Style.space(8)

              TextField {
                id: newPresetName
                width: parent.width - saveButton.width - parent.spacing
                placeholderText: "Preset name"
                foreground: root.foreground
                accent: Color.accent
                enabled: root.presetService && !root.presetService.busy
                onAccepted: saveButton.clicked()
              }
              ActionButton {
                id: saveButton
                label: "Save"
                foreground: root.foreground
                fontFamily: root.fontFamily
                enabled: root.presetService && !root.presetService.busy && newPresetName.text.trim() !== ""
                onClicked: {
                  root.presetService.capture(newPresetName.text.trim())
                  newPresetName.text = ""
                }
              }
            }
          }
        }

        BorderSurface {
          width: parent.width
          visible: root.presetService && (root.presetService.busy || root.presetService.errorMessage !== "")
          implicitHeight: statusText.implicitHeight + Style.space(18)
          radius: Style.cornerRadius
          color: root.presetService && root.presetService.errorMessage !== ""
            ? Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.12)
            : Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.08)
          borderSpec: Border.flat(
            root.presetService && root.presetService.errorMessage !== "" ? Color.urgent : Color.accent,
            1
          )

          Text {
            id: statusText
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(9)
            wrapMode: Text.Wrap
            text: root.presetService ? root.presetService.statusMessage : "Workspace Presets service unavailable"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }

        BorderSurface {
          width: parent.width
          visible: root.presetService && !root.presetService.capabilities.ready
          implicitHeight: requirementsColumn.implicitHeight + Style.space(18)
          radius: Style.cornerRadius
          color: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.10)
          borderSpec: Border.flat(Color.urgent, 1)

          Column {
            id: requirementsColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(9)
            spacing: Style.space(4)
            Text {
              text: "System requirements are not met"
              color: Color.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }
            Text {
              width: parent.width
              wrapMode: Text.Wrap
              text: root.presetService
                ? "Requires Omarchy 4.0+, Hyprland 0.56+, Python 3, uwsm-app, and gtk-launch. Missing: " + (root.presetService.capabilities.missingCommands || []).join(", ")
                : ""
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }
        }

        BorderSurface {
          width: parent.width
          visible: root.presetService && root.presetService.pendingPreflight !== null
          implicitHeight: confirmLoadColumn.implicitHeight + Style.space(20)
          radius: Style.cornerRadius
          color: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.10)
          borderSpec: Border.flat(Color.urgent, 1)

          Column {
            id: confirmLoadColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(10)
            spacing: Style.space(8)
            Text {
              text: "Replace this workspace?"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }
            Text {
              width: parent.width
              wrapMode: Text.Wrap
              text: {
                if (!root.presetService || !root.presetService.pendingPreflight) return ""
                var check = root.presetService.pendingPreflight
                var message = (check.windowsToClose || []).length + " current window(s) will receive normal close requests."
                if ((check.conflicts || []).length > 0)
                  message += " " + check.conflicts.length + " matching window(s) already exist on other workspaces."
                return message + " Applications that refuse to close will never be force-killed."
              }
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Row {
              spacing: Style.space(8)
              ActionButton {
                label: "Cancel"
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.presetService.cancelPreflight()
              }
              ActionButton {
                label: "Launch new"
                foreground: root.foreground
                fontFamily: root.fontFamily
                destructive: true
                onClicked: root.presetService.confirmLoad("launch-new")
              }
              ActionButton {
                label: "Move existing"
                foreground: root.foreground
                fontFamily: root.fontFamily
                destructive: true
                visible: root.presetService && root.presetService.pendingPreflight && (root.presetService.pendingPreflight.conflicts || []).length > 0
                onClicked: root.presetService.confirmLoad("move-existing")
              }
            }
          }
        }

        BorderSurface {
          width: parent.width
          visible: root.confirmPreset !== null
          implicitHeight: destructiveColumn.implicitHeight + Style.space(18)
          radius: Style.cornerRadius
          color: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.10)
          borderSpec: Border.flat(Color.urgent, 1)

          Column {
            id: destructiveColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(9)
            spacing: Style.space(8)
            Text {
              width: parent.width
              wrapMode: Text.Wrap
              text: root.confirmAction === "delete"
                ? "Delete ‘" + (root.confirmPreset ? root.confirmPreset.name : "") + "’?"
                : "Overwrite ‘" + (root.confirmPreset ? root.confirmPreset.name : "") + "’ with the current workspace?"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
            }
            Row {
              spacing: Style.space(8)
              ActionButton {
                label: "Cancel"
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: { root.confirmAction = ""; root.confirmPreset = null }
              }
              ActionButton {
                label: root.confirmAction === "delete" ? "Delete" : "Overwrite"
                foreground: root.foreground
                fontFamily: root.fontFamily
                destructive: true
                onClicked: {
                  if (root.confirmAction === "delete") root.presetService.deletePreset(root.confirmPreset.id)
                  else root.presetService.overwrite(root.confirmPreset.id, root.confirmPreset.name)
                  root.confirmAction = ""
                  root.confirmPreset = null
                }
              }
            }
          }
        }

        Text {
          text: "Saved presets"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }

        Text {
          width: parent.width
          visible: root.presetService && root.presetService.presets.length === 0 && !root.presetService.busy
          text: "Save this workspace to create your first preset."
          color: Color.muted
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
        }

        Repeater {
          model: root.presetService ? root.presetService.presets : []

          BorderSurface {
            id: presetCard
            required property var modelData
            width: content.width
            implicitHeight: presetColumn.implicitHeight + Style.space(20)
            radius: Style.cornerRadius
            color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.035)
            borderSpec: Border.flat(Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12), 1)

            Column {
              id: presetColumn
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(10)
              spacing: Style.space(8)

              Row {
                width: parent.width
                spacing: Style.space(8)

                Column {
                  width: parent.width - layoutBadge.width - parent.spacing
                  spacing: Style.space(2)
                  Text {
                    visible: root.editingId !== presetCard.modelData.id
                    width: parent.width
                    text: presetCard.modelData.name
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                    elide: Text.ElideRight
                  }
                  TextField {
                    visible: root.editingId === presetCard.modelData.id
                    width: parent.width
                    text: root.editingId === presetCard.modelData.id ? root.editingName : ""
                    foreground: root.foreground
                    accent: Color.accent
                    onTextChanged: if (root.editingId === presetCard.modelData.id) root.editingName = text
                    onAccepted: {
                      if (text.trim() !== "") root.presetService.renamePreset(presetCard.modelData.id, text.trim())
                      root.editingId = ""
                    }
                  }
                  Text {
                    text: presetCard.modelData.windowCount + " window(s) · Updated " + presetCard.modelData.updatedAt
                    color: Color.muted
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                }

                BorderSurface {
                  id: layoutBadge
                  implicitWidth: badgeText.implicitWidth + Style.space(12)
                  implicitHeight: Style.space(24)
                  radius: implicitHeight / 2
                  color: Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.10)
                  Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: presetCard.modelData.layout
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                }
              }

              Text {
                visible: !presetCard.modelData.loadable
                text: presetCard.modelData.unresolvedCount + " launcher(s) need setup before this preset can load"
                color: Color.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Row {
                spacing: Style.space(7)
                ActionButton {
                  label: presetCard.modelData.loadable ? "Load" : "Set up"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  enabled: root.presetService && !root.presetService.busy
                  onClicked: {
                    if (presetCard.modelData.loadable) root.presetService.preflight(presetCard.modelData.id)
                    else {
                      root.presetService.loadDetails(presetCard.modelData.id)
                      root.presetService.loadDesktopEntries()
                    }
                  }
                }
                ActionButton {
                  label: root.editingId === presetCard.modelData.id ? "Cancel rename" : "Rename"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  enabled: root.presetService && !root.presetService.busy
                  onClicked: {
                    if (root.editingId === presetCard.modelData.id) root.editingId = ""
                    else { root.editingId = presetCard.modelData.id; root.editingName = presetCard.modelData.name }
                  }
                }
                ActionButton {
                  label: "Overwrite"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  enabled: root.presetService && !root.presetService.busy
                  onClicked: { root.confirmAction = "overwrite"; root.confirmPreset = presetCard.modelData }
                }
                ActionButton {
                  label: "Delete"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  destructive: true
                  enabled: root.presetService && !root.presetService.busy
                  onClicked: { root.confirmAction = "delete"; root.confirmPreset = presetCard.modelData }
                }
              }
            }
          }
        }

        BorderSurface {
          width: parent.width
          visible: root.presetService && root.presetService.selectedDetails !== null
          implicitHeight: resolverColumn.implicitHeight + Style.space(20)
          radius: Style.cornerRadius
          color: Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.06)
          borderSpec: Border.flat(Color.accent, 1)

          Column {
            id: resolverColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(10)
            spacing: Style.space(9)

            Row {
              width: parent.width
              Text {
                width: parent.width - closeResolver.width
                text: "Launcher setup · " + (root.presetService && root.presetService.selectedDetails ? root.presetService.selectedDetails.name : "")
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.subtitle
                font.bold: true
              }
              ActionButton {
                id: closeResolver
                label: "Close"
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.presetService.selectedDetails = null
              }
            }

            Repeater {
              model: {
                if (!root.presetService || !root.presetService.selectedDetails) return []
                return (root.presetService.selectedDetails.snapshot.windows || []).filter(function(slot) { return !slot.launcher })
              }

              BorderSurface {
                id: resolverCard
                required property var modelData
                property string validationError: ""
                width: resolverColumn.width
                implicitHeight: resolverSlotColumn.implicitHeight + Style.space(16)
                radius: Style.cornerRadius
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.035)
                borderSpec: Border.flat(Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12), 1)

                Column {
                  id: resolverSlotColumn
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.margins: Style.space(8)
                  spacing: Style.space(7)
                  Text {
                    width: parent.width
                    text: (resolverCard.modelData.match.class || "Unknown application") + " · " + (resolverCard.modelData.match.title || "Untitled window")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                    elide: Text.ElideRight
                  }
                  Repeater {
                    model: (resolverCard.modelData.launcherCandidates || []).slice(0, 4)
                    ActionButton {
                      required property var modelData
                      label: "Use " + modelData.name + " (" + modelData.desktopId + ")"
                      foreground: root.foreground
                      fontFamily: root.fontFamily
                      onClicked: root.presetService.setDesktopLauncher(
                        root.presetService.selectedDetails.id,
                        resolverCard.modelData.id,
                        modelData.desktopId
                      )
                    }
                  }
                  Row {
                    width: parent.width
                    spacing: Style.space(7)
                    TextField {
                      id: desktopIdField
                      width: parent.width - desktopIdButton.width - parent.spacing
                      placeholderText: "Desktop ID, for example foot.desktop"
                      foreground: root.foreground
                      accent: Color.accent
                    }
                    ActionButton {
                      id: desktopIdButton
                      label: "Set desktop"
                      foreground: root.foreground
                      fontFamily: root.fontFamily
                      enabled: desktopIdField.text.trim().endsWith(".desktop")
                      onClicked: root.presetService.setDesktopLauncher(
                        root.presetService.selectedDetails.id,
                        resolverCard.modelData.id,
                        desktopIdField.text.trim()
                      )
                    }
                  }
                  Row {
                    width: parent.width
                    spacing: Style.space(7)
                    TextField {
                      id: commandField
                      width: parent.width - commandButton.width - parent.spacing
                      placeholderText: "Custom argv JSON, for example [\"foot\"]"
                      foreground: root.foreground
                      accent: Color.accent
                    }
                    ActionButton {
                      id: commandButton
                      label: "Set command"
                      foreground: root.foreground
                      fontFamily: root.fontFamily
                      enabled: commandField.text.trim() !== ""
                      onClicked: {
                        try {
                          var argv = JSON.parse(commandField.text)
                          if (!Array.isArray(argv) || argv.length === 0) throw new Error("Expected an argv array")
                          root.presetService.setCommandLauncher(
                            root.presetService.selectedDetails.id,
                            resolverCard.modelData.id,
                            argv
                          )
                          resolverCard.validationError = ""
                        } catch (e) {
                          resolverCard.validationError = String(e)
                        }
                      }
                    }
                  }
                  Text {
                    visible: resolverCard.validationError !== ""
                    text: resolverCard.validationError
                    color: Color.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                }
              }
            }
          }
        }

        Item { width: 1; height: Style.space(2) }
      }
    }
  }
}
