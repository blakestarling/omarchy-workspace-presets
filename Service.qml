import QtQuick
import Quickshell.Io

Item {
  id: root

  property var shell: null
  property var manifest: null
  readonly property string moduleName: "blakestarling.workspace-presets"
  readonly property string sourceDir: manifest && manifest.__sourceDir ? String(manifest.__sourceDir) : ""
  readonly property string backendPath: sourceDir === "" ? "" : sourceDir + "/backend/main.py"

  property var presets: []
  property var capabilities: ({ ready: false, missingCommands: [] })
  property bool capabilitiesChecked: false
  property var selectedDetails: null
  property var desktopEntries: []
  property var pendingPreflight: null
  property var lastResult: null
  property string currentOperation: ""
  property string progressStage: ""
  property string statusMessage: "Starting Workspace Presets…"
  property string errorMessage: ""
  property bool busy: false
  property bool initialized: false
  property var commandQueue: []
  property bool refreshAfterCurrent: false

  signal changed()

  function initialize() {
    if (initialized || backendPath === "") return
    initialized = true
    enqueue(["capabilities"], "capabilities")
    enqueue(["list"], "list")
  }

  onManifestChanged: Qt.callLater(root.initialize)
  Component.onCompleted: Qt.callLater(root.initialize)

  // The shell injects manifest immediately after createObject(). Depending on
  // binding evaluation order, sourceDir can become non-empty one tick after
  // manifestChanged. Retry only during startup and stop permanently once the
  // first command has been queued.
  Timer {
    interval: 100
    repeat: true
    running: !root.initialized
    onTriggered: root.initialize()
  }

  function enqueue(args, operation, refreshAfter) {
    var next = commandQueue.slice()
    next.push({ args: args, operation: operation, refreshAfter: refreshAfter === true })
    commandQueue = next
    startNext()
  }

  function startNext() {
    if (busy || commandQueue.length === 0 || backendPath === "") return
    var next = commandQueue[0]
    commandQueue = commandQueue.slice(1)
    currentOperation = next.operation
    refreshAfterCurrent = next.refreshAfter
    progressStage = next.operation
    statusMessage = "Working…"
    errorMessage = ""
    lastResult = null
    backend.stderrText = ""
    backend.hadStructuredError = false
    // Omarchy watches every file under an installed plugin. Python bytecode
    // caches would therefore reload this service while its first command is
    // still running, so the worker must leave the checkout completely inert.
    backend.command = ["python3", "-B", backendPath].concat(next.args)
    busy = true
    backend.running = true
  }

  function refresh() { enqueue(["list"], "list") }
  function refreshCapabilities() { enqueue(["capabilities"], "capabilities") }
  function loadDetails(presetId) { enqueue(["details", "--id", String(presetId)], "details") }
  function loadDesktopEntries() {
    if (desktopEntries.length === 0) enqueue(["desktop-entries"], "desktop-entries")
  }

  function capture(name) {
    enqueue(["capture", "--name", String(name)], "capture", true)
  }

  function overwrite(presetId, name) {
    enqueue(
      ["capture", "--name", String(name), "--overwrite-id", String(presetId)],
      "overwrite",
      true
    )
  }

  function renamePreset(presetId, name) {
    enqueue(["rename", "--id", String(presetId), "--name", String(name)], "rename", true)
  }

  function deletePreset(presetId) {
    enqueue(["delete", "--id", String(presetId)], "delete", true)
  }

  function preflight(presetId) {
    pendingPreflight = null
    enqueue(["preflight", "--id", String(presetId)], "preflight")
  }

  function cancelPreflight() {
    pendingPreflight = null
    statusMessage = "Load cancelled"
  }

  function confirmLoad(conflictPolicy) {
    if (!pendingPreflight || !pendingPreflight.preset || !pendingPreflight.workspace) return
    var presetId = String(pendingPreflight.preset.id)
    var workspaceId = String(pendingPreflight.workspace.id)
    pendingPreflight = null
    enqueue(
      [
        "load", "--id", presetId,
        "--expected-workspace-id", workspaceId,
        "--conflict-policy", String(conflictPolicy),
        "--confirmed"
      ],
      "load",
      true
    )
  }

  function setDesktopLauncher(presetId, slotId, desktopId) {
    enqueue(
      ["set-launcher", "--id", String(presetId), "--slot-id", String(slotId), "--desktop-id", String(desktopId)],
      "set-launcher",
      true
    )
  }

  function setCommandLauncher(presetId, slotId, argv) {
    enqueue(
      ["set-launcher", "--id", String(presetId), "--slot-id", String(slotId), "--argv-json", JSON.stringify(argv)],
      "set-launcher",
      true
    )
  }

  function handleLine(raw) {
    var line = String(raw).trim()
    if (line === "") return
    var event
    try {
      event = JSON.parse(line)
    } catch (e) {
      errorMessage = "Backend returned unreadable output"
      return
    }
    if (event.type === "progress") {
      progressStage = String(event.stage || currentOperation)
      statusMessage = String(event.message || "Working…")
      return
    }
    if (event.type === "error") {
      backend.hadStructuredError = true
      errorMessage = String(event.message || "Workspace Presets failed")
      statusMessage = errorMessage
      lastResult = event
      if (currentOperation === "capabilities") {
        capabilitiesChecked = true
        capabilities = ({ ready: false, missingCommands: [], error: errorMessage })
      }
      return
    }
    if (event.type !== "result") return
    lastResult = event.data
    var operation = String(event.operation || currentOperation)
    if (operation === "list") {
      presets = Array.isArray(event.data) ? event.data : []
      statusMessage = presets.length === 0 ? "No presets saved yet" : "Ready"
    } else if (operation === "capabilities") {
      capabilitiesChecked = true
      capabilities = event.data || ({ ready: false })
      if (!capabilities.ready) statusMessage = "System requirements are not met"
    } else if (operation === "details") {
      selectedDetails = event.data || null
      statusMessage = "Choose a launcher for each unresolved window"
    } else if (operation === "desktop-entries") {
      desktopEntries = Array.isArray(event.data) ? event.data : []
    } else if (operation === "preflight") {
      pendingPreflight = event.data || null
      statusMessage = "Confirm workspace replacement"
    } else {
      if (operation === "set-launcher") selectedDetails = null
      statusMessage = operation === "load" ? "Preset loaded" : "Preset updated"
    }
    changed()
  }

  Process {
    id: backend
    property string stderrText: ""
    property bool hadStructuredError: false

    stdout: SplitParser {
      onRead: function(line) { root.handleLine(line) }
    }
    stderr: SplitParser {
      onRead: function(line) {
        var value = String(line).trim()
        if (value !== "") backend.stderrText += (backend.stderrText === "" ? "" : "\n") + value
      }
    }
    onExited: function(exitCode) {
      root.busy = false
      var succeeded = exitCode === 0 && !backend.hadStructuredError
      if (exitCode !== 0 && !backend.hadStructuredError) {
        root.errorMessage = backend.stderrText || "Backend exited with status " + exitCode
        root.statusMessage = root.errorMessage
        if (root.currentOperation === "capabilities") {
          root.capabilitiesChecked = true
          root.capabilities = ({ ready: false, missingCommands: [], error: root.errorMessage })
        }
      }
      if (root.refreshAfterCurrent) {
        root.refreshAfterCurrent = false
        // A refresh starts a new command, which intentionally clears the
        // current error. Only refresh after success so failed restores remain
        // visible instead of appearing to do nothing.
        if (succeeded) root.enqueue(["list"], "list")
      }
      Qt.callLater(root.startNext)
    }
  }
}
