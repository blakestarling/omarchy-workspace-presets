import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "blakestarling.workspace-presets"

  readonly property var presetService: bar?.shell?.serviceFor(moduleName)
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function open() { if (panelLoader.item) panelLoader.item.openFromHotkey() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
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
        pendingPreflight: root.presetService.pendingPreflight,
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
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.presetService && root.presetService.busy ? "󰔟" : "󰆓"
    slotSize: Style.bar.statusSlot
    tooltipText: root.presetService
      ? (root.presetService.errorMessage || root.presetService.statusMessage || "Workspace Presets")
      : "Workspace Presets"

    onPressed: function(mouseButton) {
      if (mouseButton === Qt.MiddleButton && root.presetService) root.presetService.refresh()
      else root.togglePanel()
    }
  }
}
