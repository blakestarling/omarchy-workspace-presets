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
  property var presetGroups: []
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
  property int loadStartedSerial: 0

  signal changed()
  signal confirmationRequested()

  function initialize() {
    if (initialized || backendPath === "") return
    initialized = true
    enqueue(["capabilities"], "capabilities")
    enqueue(["resolve-launchers"], "resolve-launchers")
    enqueue(["list"], "list")
    enqueue(["groups"], "groups")
    enqueue(["startup-group"], "startup-group")
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

  function refresh() {
    enqueue(["list"], "list")
    enqueue(["groups"], "groups")
  }
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

  function createGroup(name) {
    enqueue(["group-create", "--name", String(name)], "group-create", true)
  }

  function renameGroup(groupId, name) {
    enqueue(["group-rename", "--id", String(groupId), "--name", String(name)], "group-rename", true)
  }

  function assignPreset(groupId, presetId, workspace) {
    enqueue([
      "group-assign", "--id", String(groupId), "--preset-id", String(presetId),
      "--workspace", String(workspace)
    ], "group-assign", true)
  }

  function unassignPreset(groupId, presetId) {
    enqueue([
      "group-unassign", "--id", String(groupId), "--preset-id", String(presetId)
    ], "group-unassign", true)
  }

  function deleteGroup(groupId) {
    enqueue(["group-delete", "--id", String(groupId)], "group-delete", true)
  }

  function setStartupGroup(groupId, enabled) {
    var args = enabled ? ["group-startup", "--id", String(groupId)] : ["group-startup", "--disable"]
    enqueue(args, "group-startup", true)
  }

  function setStartupConfirmation(enabled) {
    enqueue(
      ["group-startup-confirmation", enabled ? "--enable" : "--disable"],
      "group-startup-confirmation",
      true
    )
  }

  function preflightGroup(groupId) {
    pendingPreflight = null
    enqueue(["group-preflight", "--id", String(groupId)], "group-preflight")
  }

  function preflight(presetId) {
    pendingPreflight = null
    enqueue(["preflight", "--id", String(presetId)], "preflight")
  }

  function cancelPreflight() {
    var wasStartup = pendingPreflight && pendingPreflight.startupConfirmation === true
    pendingPreflight = null
    statusMessage = wasStartup ? "Startup launch cancelled for this session" : "Load cancelled"
  }

  function enqueueConfirmedLoad(check, conflictPolicy) {
    if (!check) return
    if (check.kind === "group" && check.group) {
      enqueue(
        ["group-load", "--id", String(check.group.id), "--expected-token", String(check.token), "--confirmed"],
        "group-load", true
      )
      loadStartedSerial += 1
      return
    }
    if (!check.preset || !check.workspace) return
    enqueue(
      [
        "load", "--id", String(check.preset.id),
        "--expected-workspace-id", String(check.workspace.id),
        "--expected-token", String(check.token),
        "--conflict-policy", String(conflictPolicy),
        "--confirmed"
      ],
      "load",
      true
    )
    loadStartedSerial += 1
  }

  function confirmLoad(conflictPolicy) {
    if (!pendingPreflight) return
    var check = pendingPreflight
    pendingPreflight = null
    enqueueConfirmedLoad(check, conflictPolicy)
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
    } else if (operation === "groups") {
      presetGroups = Array.isArray(event.data) ? event.data : []
      statusMessage = "Ready"
    } else if (operation === "capabilities") {
      capabilitiesChecked = true
      capabilities = event.data || ({ ready: false })
      if (!capabilities.ready) statusMessage = "System requirements are not met"
    } else if (operation === "details") {
      selectedDetails = event.data || null
      statusMessage = "Choose a launcher for each unresolved window"
    } else if (operation === "desktop-entries") {
      desktopEntries = Array.isArray(event.data) ? event.data : []
    } else if (operation === "resolve-launchers") {
      var repaired = event.data && Number(event.data.resolvedWindowCount) || 0
      var normalized = event.data && Number(event.data.normalizedLauncherCount) || 0
      if (repaired + normalized > 0)
        statusMessage = "Resolved " + (repaired + normalized) + " saved launcher(s)"
    } else if (operation === "preflight" || operation === "group-preflight") {
      var check = event.data || null
      var windowsToClose = check && Array.isArray(check.windowsToClose) ? check.windowsToClose : []
      var conflicts = check && Array.isArray(check.conflicts) ? check.conflicts : []
      var requiresConfirmation = check && check.requiresConfirmation !== undefined
        ? check.requiresConfirmation === true
        : windowsToClose.length > 0 || conflicts.length > 0
      if (check && !requiresConfirmation) {
        pendingPreflight = null
        statusMessage = check.kind === "group" ? "Group workspaces are clear — loading" : "Workspace is clear — loading preset"
        enqueueConfirmedLoad(check, "launch-new")
      } else {
        pendingPreflight = check
        statusMessage = "Confirm workspace replacement"
        confirmationRequested()
      }
    } else {
      if (operation === "set-launcher") selectedDetails = null
      if (operation === "startup-group") {
        if (event.data && event.data.confirmationRequired && event.data.preflight) {
          pendingPreflight = event.data.preflight
          statusMessage = "Confirm startup preset group"
          confirmationRequested()
        } else if (event.data && event.data.launched) {
          statusMessage = "Startup preset group loaded"
        } else statusMessage = "Ready"
      } else if (operation === "group-load") statusMessage = "Preset group loaded"
      else statusMessage = operation === "load" ? "Preset loaded" : "Preset updated"
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
        if (succeeded) {
          root.enqueue(["list"], "list")
          root.enqueue(["groups"], "groups")
        }
      }
      Qt.callLater(root.startNext)
    }
  }
}
