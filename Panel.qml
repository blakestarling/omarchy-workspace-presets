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
  property string editingGroupId: ""
  property string editingGroupName: ""
  property var confirmGroup: null
  property string activeTab: "presets"
  property string presetSearch: ""
  property string groupSearch: ""
  property string presetSort: "recent"
  property string groupSort: "recent"
  // Delegate instances are recreated when group data is refreshed or sorted.
  // Keep in-progress workspace edits outside the delegates so unrelated saves
  // cannot discard what the user has typed.
  property var assignmentDrafts: ({})
  readonly property var visiblePresets: filteredPresets()
  readonly property var visibleGroups: filteredGroups()
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
    editingGroupId = ""
    confirmGroup = null
    assignmentDrafts = ({})
    if (presetService) {
      presetService.selectedDetails = null
      if (presetService.pendingPreflight !== null) presetService.cancelPreflight()
    }
    controller.hide()
  }

  function toggle() { opened ? close() : openFromHotkey() }

  Connections {
    target: root.presetService
    function onLoadStarted() { root.close() }
  }

  function workspaceFor(group, presetId) {
    var assignments = group && Array.isArray(group.assignments) ? group.assignments : []
    for (var index = 0; index < assignments.length; index++)
      if (assignments[index].presetId === presetId) return Number(assignments[index].workspace)
    return -1
  }

  function currentGroup(groupId) {
    var groups = presetService && Array.isArray(presetService.presetGroups)
      ? presetService.presetGroups : []
    for (var index = 0; index < groups.length; index++)
      if (groups[index].id === groupId) return groups[index]
    return null
  }

  function workspaceForCurrentGroup(groupId, presetId) {
    return workspaceFor(currentGroup(groupId), presetId)
  }

  function assignmentDraftKey(groupId, presetId) {
    return String(groupId) + ":" + String(presetId)
  }

  function hasAssignmentDraft(groupId, presetId) {
    return Object.prototype.hasOwnProperty.call(
      assignmentDrafts, assignmentDraftKey(groupId, presetId)
    )
  }

  function assignmentDraft(groupId, presetId) {
    return assignmentDrafts[assignmentDraftKey(groupId, presetId)]
  }

  function setAssignmentDraft(groupId, presetId, value) {
    var drafts = Object.assign({}, assignmentDrafts)
    drafts[assignmentDraftKey(groupId, presetId)] = String(value)
    assignmentDrafts = drafts
  }

  function clearAssignmentDraft(groupId, presetId) {
    var key = assignmentDraftKey(groupId, presetId)
    if (!Object.prototype.hasOwnProperty.call(assignmentDrafts, key)) return
    var drafts = Object.assign({}, assignmentDrafts)
    delete drafts[key]
    assignmentDrafts = drafts
  }

  function compareName(left, right) {
    return String(left.name || "").toLocaleLowerCase()
      .localeCompare(String(right.name || "").toLocaleLowerCase())
  }

  function compareRecent(left, right) {
    var leftUsed = String(left.lastUsedAt || "")
    var rightUsed = String(right.lastUsedAt || "")
    if (leftUsed !== rightUsed) return rightUsed.localeCompare(leftUsed)
    return String(right.updatedAt || "").localeCompare(String(left.updatedAt || ""))
  }

  function compareMostUsed(left, right) {
    var difference = Number(right.useCount || 0) - Number(left.useCount || 0)
    return difference !== 0 ? difference : compareRecent(left, right)
  }

  function filteredPresets() {
    var values = presetService && Array.isArray(presetService.presets)
      ? presetService.presets.slice() : []
    var query = presetSearch.trim().toLocaleLowerCase()
    if (query !== "") values = values.filter(function(item) {
      var windows = (item.windows || []).map(function(window) {
        return String(window.class || "") + " " + String(window.title || "")
          + " " + String(window.program || "")
      }).join(" ")
      return (String(item.name || "") + " " + String(item.layout || "") + " " + windows)
        .toLocaleLowerCase().indexOf(query) !== -1
    })
    values.sort(function(left, right) {
      if (presetSort === "most-used") return compareMostUsed(left, right)
      if (presetSort === "name") return compareName(left, right)
      if (presetSort === "updated")
        return String(right.updatedAt || "").localeCompare(String(left.updatedAt || ""))
      if (presetSort === "windows")
        return Number(right.windowCount || 0) - Number(left.windowCount || 0) || compareName(left, right)
      return compareRecent(left, right)
    })
    return values
  }

  function filteredGroups() {
    var values = presetService && Array.isArray(presetService.presetGroups)
      ? presetService.presetGroups.slice() : []
    var query = groupSearch.trim().toLocaleLowerCase()
    if (query !== "") values = values.filter(function(item) {
      var assignments = (item.assignments || []).map(function(assignment) {
        return String(assignment.presetName || "") + " workspace " + String(assignment.workspace || "")
      }).join(" ")
      return (String(item.name || "") + " " + assignments).toLocaleLowerCase().indexOf(query) !== -1
    })
    values.sort(function(left, right) {
      if (groupSort === "most-used") return compareMostUsed(left, right)
      if (groupSort === "name") return compareName(left, right)
      if (groupSort === "updated")
        return String(right.updatedAt || "").localeCompare(String(left.updatedAt || ""))
      if (groupSort === "workspaces")
        return Number(right.assignmentCount || 0) - Number(left.assignmentCount || 0) || compareName(left, right)
      return compareRecent(left, right)
    })
    return values
  }

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

  component TabButton: BorderSurface {
    id: tab
    property string label: ""
    property bool selected: false
    signal clicked()

    implicitHeight: Style.space(34)
    radius: Style.cornerRadius
    color: selected
      ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.16)
      : (tabMouse.containsMouse
        ? Style.hoverFillFor(root.foreground, Color.accent)
        : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.035))
    borderSpec: Border.flat(selected ? Color.accent : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12), 1)

    Text {
      anchors.centerIn: parent
      text: tab.label
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
    }
    MouseArea {
      id: tabMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: tab.clicked()
    }
  }

  KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: true
    focusTarget: escapeKeyCatcher
    contentWidth: popup.fittedContentWidth(Style.space(620))
    contentHeight: popup.fittedContentHeight(Style.space(680))

    Item {
      id: escapeKeyCatcher
      anchors.fill: parent
      focus: true

      // A backend refresh can destroy the focused delegate after a save. A
      // window shortcut still receives Escape when no replacement item has
      // active focus, unlike the item-level fallback below.
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

        Row {
          width: parent.width
          spacing: Style.space(8)

          TabButton {
            width: (parent.width - parent.spacing) / 2
            label: "Presets" + (root.presetService ? " (" + root.presetService.presets.length + ")" : "")
            selected: root.activeTab === "presets"
            onClicked: {
              root.activeTab = "presets"
              searchField.text = root.presetSearch
              sortDropdown.value = root.presetSort
              scroll.contentY = 0
            }
          }
          TabButton {
            width: (parent.width - parent.spacing) / 2
            label: "Preset Groups" + (root.presetService ? " (" + root.presetService.presetGroups.length + ")" : "")
            selected: root.activeTab === "groups"
            onClicked: {
              root.activeTab = "groups"
              searchField.text = root.groupSearch
              sortDropdown.value = root.groupSort
              scroll.contentY = 0
            }
          }
        }

        Row {
          width: parent.width
          spacing: Style.space(8)

          TextField {
            id: searchField
            width: parent.width - sortDropdown.width - parent.spacing
            placeholderText: root.activeTab === "presets"
              ? "Search presets, apps, or layouts"
              : "Search groups, presets, or workspaces"
            text: root.activeTab === "presets" ? root.presetSearch : root.groupSearch
            foreground: root.foreground
            accent: Color.accent
            onTextChanged: {
              if (root.activeTab === "presets") root.presetSearch = text
              else root.groupSearch = text
            }
          }

          Dropdown {
            id: sortDropdown
            width: Style.space(190)
            showLabel: false
            value: root.activeTab === "presets" ? root.presetSort : root.groupSort
            options: root.activeTab === "presets" ? [
              { value: "recent", label: "Recent" },
              { value: "most-used", label: "Most used" },
              { value: "name", label: "Name A–Z" },
              { value: "updated", label: "Recently updated" },
              { value: "windows", label: "Most windows" }
            ] : [
              { value: "recent", label: "Recent" },
              { value: "most-used", label: "Most used" },
              { value: "name", label: "Name A–Z" },
              { value: "updated", label: "Recently updated" },
              { value: "workspaces", label: "Most workspaces" }
            ]
            foreground: root.foreground
            accent: Color.accent
            fontFamily: root.fontFamily
            onChanged: function(nextValue) {
              if (root.activeTab === "presets") root.presetSort = nextValue
              else root.groupSort = nextValue
            }
          }
        }

        BorderSurface {
          width: parent.width
          visible: root.activeTab === "presets"
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
          visible: root.presetService && root.presetService.capabilitiesChecked && !root.presetService.capabilities.ready
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
              text: {
                if (!root.presetService) return ""
                var capability = root.presetService.capabilities || ({})
                var parts = ["Requires Omarchy 4.0+, Hyprland 0.56+, Python 3, uwsm-app, gtk-launch, and omarchy-shell."]
                if (capability.error) parts.push(String(capability.error))
                if ((capability.missingCommands || []).length > 0)
                  parts.push("Missing: " + capability.missingCommands.join(", ") + ".")
                if (capability.omarchyVersion)
                  parts.push("Omarchy: " + capability.omarchyVersion + (capability.supportedOmarchy ? "" : " (unsupported)"))
                if (capability.hyprlandVersion)
                  parts.push("Hyprland: " + capability.hyprlandVersion + (capability.supportedHyprland ? "" : " (unsupported)"))
                return parts.join(" ")
              }
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
              text: root.presetService && root.presetService.pendingPreflight && root.presetService.pendingPreflight.kind === "group"
                ? "Replace these workspaces?" : "Replace this workspace?"
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
                if (check.kind === "group") {
                  return String(check.windowCountToClose || 0) + " window(s) across "
                    + String((check.targets || []).length) + " workspace(s) will receive normal close requests. "
                    + "Every target was validated before this confirmation. Applications that refuse to close will never be force-killed."
                }
                var workspaceName = check.workspace ? String(check.workspace.name) : "current"
                var message = (check.windowsToClose || []).length + " window(s) on workspace " + workspaceName + " will receive normal close requests."
                if ((check.conflicts || []).length > 0)
                  message += " " + check.conflicts.length + " matching window(s) already exist on other workspaces."
                return message + " Loading aborts if the active workspace changes. Applications that refuse to close will never be force-killed."
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
                  && root.presetService.pendingPreflight.kind !== "group"
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

        BorderSurface {
          width: parent.width
          visible: root.confirmGroup !== null
          implicitHeight: groupDeleteColumn.implicitHeight + Style.space(18)
          radius: Style.cornerRadius
          color: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.10)
          borderSpec: Border.flat(Color.urgent, 1)

          Column {
            id: groupDeleteColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(9)
            spacing: Style.space(8)
            Text {
              text: "Delete preset group ‘" + (root.confirmGroup ? root.confirmGroup.name : "") + "’? Presets will be kept."
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
                onClicked: root.confirmGroup = null
              }
              ActionButton {
                label: "Delete group"
                destructive: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: {
                  root.presetService.deleteGroup(root.confirmGroup.id)
                  root.confirmGroup = null
                }
              }
            }
          }
        }

        Text {
          text: "Saved presets"
          visible: root.activeTab === "presets"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }

        Text {
          width: parent.width
          visible: root.activeTab === "presets" && root.presetService
            && root.visiblePresets.length === 0 && !root.presetService.busy
          text: root.presetService && root.presetService.presets.length === 0
            ? "Save this workspace to create your first preset."
            : "No presets match your search."
          color: Color.muted
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
        }

        Repeater {
          model: root.activeTab === "presets" ? root.visiblePresets : []

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
                    text: presetCard.modelData.windowCount + " window(s) · Used "
                      + String(presetCard.modelData.useCount || 0) + " time(s)"
                      + (presetCard.modelData.lastUsedAt ? " · Last used " + presetCard.modelData.lastUsedAt : "")
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

        Text {
          text: "Preset groups"
          visible: root.activeTab === "groups"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }

        BorderSurface {
          width: parent.width
          visible: root.activeTab === "groups"
          implicitHeight: createGroupColumn.implicitHeight + Style.space(18)
          radius: Style.cornerRadius
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.04)
          borderSpec: Border.flat(Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14), 1)

          Column {
            id: createGroupColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(9)
            spacing: Style.space(7)
            Text {
              text: "Create a group, then assign each preset to a numbered workspace."
              color: Color.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Row {
              width: parent.width
              spacing: Style.space(8)
              TextField {
                id: newGroupName
                width: parent.width - createGroupButton.width - parent.spacing
                placeholderText: "Group name"
                foreground: root.foreground
                accent: Color.accent
                onAccepted: createGroupButton.clicked()
              }
              ActionButton {
                id: createGroupButton
                label: "Create group"
                foreground: root.foreground
                fontFamily: root.fontFamily
                enabled: root.presetService && !root.presetService.busy && newGroupName.text.trim() !== ""
                onClicked: {
                  root.presetService.createGroup(newGroupName.text.trim())
                  newGroupName.text = ""
                }
              }
            }
          }
        }

        Text {
          width: parent.width
          visible: root.activeTab === "groups" && root.presetService
            && root.visibleGroups.length === 0 && !root.presetService.busy
          text: root.presetService && root.presetService.presetGroups.length === 0
            ? "No preset groups yet."
            : "No preset groups match your search."
          color: Color.muted
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          horizontalAlignment: Text.AlignHCenter
        }

        Repeater {
          model: root.activeTab === "groups" ? root.visibleGroups : []

          BorderSurface {
            id: groupCard
            required property var modelData
            width: content.width
            implicitHeight: groupColumn.implicitHeight + Style.space(20)
            radius: Style.cornerRadius
            color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.035)
            borderSpec: Border.flat(
              groupCard.modelData.launchOnStartup ? Color.accent : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12), 1
            )

            Column {
              id: groupColumn
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(10)
              spacing: Style.space(8)

              Text {
                visible: root.editingGroupId !== groupCard.modelData.id
                text: groupCard.modelData.name + (groupCard.modelData.launchOnStartup ? " · Startup" : "")
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                font.bold: true
              }
              TextField {
                visible: root.editingGroupId === groupCard.modelData.id
                width: parent.width
                text: root.editingGroupId === groupCard.modelData.id ? root.editingGroupName : ""
                foreground: root.foreground
                accent: Color.accent
                onTextChanged: if (root.editingGroupId === groupCard.modelData.id) root.editingGroupName = text
                onAccepted: {
                  if (text.trim() !== "") root.presetService.renameGroup(groupCard.modelData.id, text.trim())
                  root.editingGroupId = ""
                }
              }
              Text {
                text: groupCard.modelData.assignmentCount + " workspace assignment(s)"
                  + (groupCard.modelData.loadable ? " · Ready" : " · Assign one or more ready presets")
                  + " · Used " + String(groupCard.modelData.useCount || 0) + " time(s)"
                color: groupCard.modelData.loadable ? Color.muted : Color.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Row {
                spacing: Style.space(7)
                ActionButton {
                  label: "Launch group"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  enabled: root.presetService && !root.presetService.busy && groupCard.modelData.loadable
                  onClicked: root.presetService.preflightGroup(groupCard.modelData.id)
                }
                ActionButton {
                  label: root.editingGroupId === groupCard.modelData.id ? "Cancel rename" : "Rename"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  enabled: root.presetService && !root.presetService.busy
                  onClicked: {
                    if (root.editingGroupId === groupCard.modelData.id) root.editingGroupId = ""
                    else { root.editingGroupId = groupCard.modelData.id; root.editingGroupName = groupCard.modelData.name }
                  }
                }
                ActionButton {
                  label: groupCard.modelData.launchOnStartup ? "Disable startup" : "Launch on startup"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  enabled: root.presetService && !root.presetService.busy && groupCard.modelData.loadable
                  onClicked: root.presetService.setStartupGroup(groupCard.modelData.id, !groupCard.modelData.launchOnStartup)
                }
                ActionButton {
                  label: "Delete"
                  destructive: true
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  enabled: root.presetService && !root.presetService.busy
                  onClicked: root.confirmGroup = groupCard.modelData
                }
              }

              Text {
                text: "Assignments"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                font.bold: true
              }

              Repeater {
                model: root.presetService ? root.presetService.presets : []

                Row {
                  id: assignmentRow
                  required property var modelData
                  readonly property int assignedWorkspace: root.workspaceForCurrentGroup(
                    groupCard.modelData.id, modelData.id
                  )
                  width: groupColumn.width
                  spacing: Style.space(7)
                  Text {
                    width: parent.width - workspaceField.width - assignButton.width - removeAssignment.width - parent.spacing * 3
                    text: assignmentRow.modelData.name + (assignmentRow.modelData.loadable ? "" : " (not ready)")
                    color: assignmentRow.modelData.loadable ? root.foreground : Color.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    elide: Text.ElideRight
                  }
                  TextField {
                    id: workspaceField
                    width: Style.space(72)
                    placeholderText: "0–9"
                    foreground: root.foreground
                    accent: Color.accent
                    maximumLength: 1
                    inputMethodHints: Qt.ImhDigitsOnly
                    validator: IntValidator { bottom: 0; top: 9 }
                    function syncFromSavedAssignment() {
                      var groupId = groupCard.modelData.id
                      var presetId = assignmentRow.modelData.id
                      var saved = assignmentRow.assignedWorkspace >= 0
                        ? String(assignmentRow.assignedWorkspace) : ""
                      if (root.hasAssignmentDraft(groupId, presetId)) {
                        var draft = root.assignmentDraft(groupId, presetId)
                        // Once the backend refresh contains the submitted value,
                        // it is no longer an in-progress draft.
                        if (draft === saved) root.clearAssignmentDraft(groupId, presetId)
                        text = draft
                      } else {
                        text = saved
                      }
                    }
                    Component.onCompleted: syncFromSavedAssignment()
                    onTextEdited: root.setAssignmentDraft(
                      groupCard.modelData.id, assignmentRow.modelData.id, text
                    )
                    onAccepted: assignButton.clicked()
                    Connections {
                      target: root.presetService
                      function onPresetGroupsChanged() { workspaceField.syncFromSavedAssignment() }
                    }
                  }
                  ActionButton {
                    id: assignButton
                    label: assignmentRow.assignedWorkspace >= 0 ? "Update" : "Assign"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    enabled: root.presetService && !root.presetService.busy && assignmentRow.modelData.loadable
                      && /^[0-9]$/.test(workspaceField.text.trim())
                    onClicked: root.presetService.assignPreset(
                      groupCard.modelData.id, assignmentRow.modelData.id, Number(workspaceField.text.trim())
                    )
                  }
                  ActionButton {
                    id: removeAssignment
                    label: "Remove"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    visible: assignmentRow.assignedWorkspace >= 0
                    enabled: root.presetService && !root.presetService.busy
                    onClicked: root.presetService.unassignPreset(groupCard.modelData.id, assignmentRow.modelData.id)
                  }
                }
              }
            }
          }
        }

        BorderSurface {
          width: parent.width
          visible: root.activeTab === "presets" && root.presetService
            && root.presetService.selectedDetails !== null
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
}
