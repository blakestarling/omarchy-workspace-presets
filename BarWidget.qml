import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "blakestarling.workspace-presets"

  readonly property var presetService: bar?.shell?.serviceFor(moduleName)
  // Set when open() arrives before the panel component has finished loading.
  property bool pendingOpen: false
  readonly property bool opened: (panelLoader.item && panelLoader.item.opened === true)
    || (confirmationLoader.item && confirmationLoader.item.opened === true)
  readonly property bool popoutSwitchClosing:
    (panelLoader.item && panelLoader.item.popoutSwitchClosing === true)
    || (confirmationLoader.item && confirmationLoader.item.popoutSwitchClosing === true)

  function injectPanel(target) {
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  // Panel.qml is ~200 items once its components expand, built per bar. Nothing
  // needs it until someone opens it, so it is loaded on first use and then
  // kept - reopening is common, and a second build would be visible.
  function open() {
    if (root.presetService && root.presetService.pendingPreflight) {
      showConfirmation()
      return
    }
    panelLoader.active = true
    if (panelLoader.item) panelLoader.item.openFromHotkey()
    else pendingOpen = true
  }
  function close() {
    confirmationDelay.stop()
    if (confirmationLoader.item && confirmationLoader.item.opened)
      confirmationLoader.item.close()
    if (panelLoader.item && panelLoader.item.opened) panelLoader.item.close()
  }
  function togglePanel() { root.opened ? root.close() : root.open() }

  function closeForPopoutSwitch() {
    confirmationDelay.stop()
    if (confirmationLoader.item && confirmationLoader.item.opened)
      confirmationLoader.item.closeForPopoutSwitch()
    if (panelLoader.item && panelLoader.item.opened)
      panelLoader.item.closeForPopoutSwitch()
  }

  // Bar tooltips are drawn by shell chrome whose Text element leaves
  // textFormat at Text.AutoText, so a '<' anywhere in the string makes Qt
  // parse the whole message as rich text. Backend messages can embed window
  // classes and titles, which any application - including a remote page -
  // controls. Strip markup characters before the string leaves this plugin.
  function plainTooltip(value) {
    return String(value === undefined || value === null ? "" : value).replace(/[<>&]/g, " ")
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: {
    injectPanel(panelLoader.item)
    injectPanel(confirmationLoader.item)
  }
  onSettingsChanged: {
    injectPanel(panelLoader.item)
    injectPanel(confirmationLoader.item)
  }

  function showConfirmation() {
    if (!root.presetService || !root.presetService.pendingPreflight) return
    confirmationLoader.active = true
    if (confirmationLoader.item && confirmationLoader.item.opened) return
    if (panelLoader.item && panelLoader.item.opened) {
      panelLoader.item.closeForConfirmation()
      confirmationDelay.restart()
    } else if (confirmationLoader.item) {
      confirmationLoader.item.open()
    }
  }

  Timer {
    id: confirmationDelay
    interval: 150
    repeat: false
    onTriggered: if (root.presetService && root.presetService.pendingPreflight
      && confirmationLoader.item) confirmationLoader.item.open()
  }

  Connections {
    target: root.presetService
    // The widget may be constructed one tick before the service registry has
    // published this plugin's service object.
    ignoreUnknownSignals: true
    function onConfirmationRequested() { root.showConfirmation() }
  }

  Loader {
    id: panelLoader
    active: false
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel(panelLoader.item)
      Qt.callLater(function() {
        root.injectPanel(panelLoader.item)
        if (root.pendingOpen) {
          root.pendingOpen = false
          panelLoader.item.openFromHotkey()
        }
      })
    }
  }

  Loader {
    id: confirmationLoader
    active: false
    source: Qt.resolvedUrl("ConfirmationPanel.qml")
    visible: false
    onLoaded: {
      root.injectPanel(confirmationLoader.item)
      Qt.callLater(function() {
        root.injectPanel(confirmationLoader.item)
        root.showConfirmation()
      })
    }
  }

  IpcHandler {
    target: root.moduleName

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
    function refresh(): void { if (root.presetService) root.presetService.refresh() }
    function status(): string {
      if (!root.presetService) return JSON.stringify({ available: false })
      return JSON.stringify({
        available: true,
        initialized: root.presetService.initialized,
        sourceDir: root.presetService.sourceDir,
        backendPath: root.presetService.backendPath,
        capabilitiesChecked: root.presetService.capabilitiesChecked,
        capabilities: root.presetService.capabilities,
        busy: root.presetService.busy,
        currentOperation: root.presetService.currentOperation,
        queuedCommands: root.presetService.commandQueue.length,
        workerReady: root.presetService.workerReady,
        workerFailedToStart: root.presetService.workerFailedToStart,
        pendingPreflight: root.presetService.pendingPreflight,
        presetGroupCount: root.presetService.presetGroups.length,
        lastResult: root.presetService.lastResult,
        statusMessage: root.presetService.statusMessage,
        errorMessage: root.presetService.errorMessage
      })
    }
    function save(name: string): string {
      if (!root.presetService) return "service unavailable"
      root.presetService.capture(name)
      return "capture started"
    }
    function load(presetId: string): string {
      if (!root.presetService) return "service unavailable"
      root.presetService.preflight(presetId)
      root.open()
      return "preflight started"
    }
    function loadGroup(groupId: string): string {
      if (!root.presetService) return "service unavailable"
      root.presetService.preflightGroup(groupId)
      root.open()
      return "group preflight started"
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.presetService && root.presetService.busy ? "󰔟" : "󰆓"
    slotSize: Style.bar.statusSlot
    tooltipText: root.presetService
      ? root.plainTooltip(
          root.presetService.errorMessage || root.presetService.statusMessage || "Workspace Presets"
        )
      : "Workspace Presets"

    onPressed: function(mouseButton) {
      if (mouseButton === Qt.MiddleButton && root.presetService) root.presetService.refresh()
      else root.togglePanel()
    }
  }
}
