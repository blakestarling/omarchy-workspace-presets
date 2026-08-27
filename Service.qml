import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  property var shell: null
  property var manifest: null
  readonly property string moduleName: "blakestarling.workspace-presets"
  readonly property string sourceDir: manifest && manifest.__sourceDir ? String(manifest.__sourceDir) : ""
  readonly property string backendPath: sourceDir === "" ? "" : sourceDir + "/backend/main.py"

  // Omarchy watches every file under an installed plugin, so Python bytecode
  // caches written next to the source would reload this service while its
  // first command is still running. Relocating the cache keeps the checkout
  // inert without paying to recompile every module on every command, which
  // -B did.
  readonly property string bytecodeCacheDir: {
    var cache = String(Quickshell.env("XDG_CACHE_HOME") || "")
    if (cache === "") {
      var home = String(Quickshell.env("HOME") || "")
      cache = home === "" ? String(Quickshell.cacheDir || "") : home + "/.cache"
    }
    return cache === "" ? "" : cache + "/omarchy-workspace-presets/pycache"
  }

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
  // The worker answers one request at a time and tags every event it emits
  // with the id it was given, so a reply that arrives after a restart can be
  // recognised as stale rather than credited to the wrong command.
  property int requestSerial: 0
  property int currentRequestId: 0
  property bool workerReady: false
  property bool workerFailedToStart: false

  signal changed()
  signal confirmationRequested()

  function initialize() {
    if (initialized || backendPath === "") return
    initialized = true
    // The startup launch is the only queued command anyone is waiting on at
    // login, so nothing that merely fills a closed panel runs ahead of it.
    // resolve-launchers stays first because it can repair a draft the startup
    // group depends on.
    enqueue(["resolve-launchers"], "resolve-launchers")
    enqueue(["startup-group"], "startup-group")
    enqueue(["capabilities"], "capabilities")
    enqueue(["state"], "state")
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
    workerFailedToStart = false
    var next = commandQueue.slice()
    next.push({ args: args, operation: operation, refreshAfter: refreshAfter === true })
    commandQueue = next
    startNext()
  }

  function startNext() {
    if (busy || commandQueue.length === 0 || backendPath === "") return
    if (!backend.running) {
      // Starting an interpreter and importing the backend cost more than most
      // commands do, so one worker serves them all and is restarted only when
      // it exits - on its own idle timeout, or because it failed.
      workerReady = false
      backend.stderrText = ""
      backend.command = ["python3", backendPath, "serve"]
      backend.running = true
      return
    }
    if (!workerReady) return
    var next = commandQueue[0]
    commandQueue = commandQueue.slice(1)
    currentOperation = next.operation
    refreshAfterCurrent = next.refreshAfter
    progressStage = next.operation
    statusMessage = "Working…"
    errorMessage = ""
    lastResult = null
    backend.hadStructuredError = false
    requestSerial += 1
    currentRequestId = requestSerial
    busy = true
    backend.write(JSON.stringify({ id: currentRequestId, args: next.args }) + "\n")
  }

  function finishCommand(succeeded) {
    busy = false
    currentRequestId = 0
    if (refreshAfterCurrent) {
      refreshAfterCurrent = false
      // A refresh starts a new command, which intentionally clears the
      // current error. Only refresh after success so failed restores remain
      // visible instead of appearing to do nothing.
      if (succeeded) enqueue(["state"], "state")
    }
    Qt.callLater(root.startNext)
  }

  function failCurrentCommand(message) {
    if (!busy) return
    errorMessage = message
    statusMessage = message
    if (currentOperation === "capabilities") {
      capabilitiesChecked = true
      capabilities = ({ ready: false, missingCommands: [], error: message })
    }
    finishCommand(false)
    changed()
  }

  function refresh() { enqueue(["state"], "state") }
  function refreshCapabilities() {
    enqueue(["capabilities", "--refresh"], "capabilities")
  }
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
    if (event.type === "ready") {
      workerReady = true
      workerFailedToStart = false
      Qt.callLater(root.startNext)
      return
    }
    // A reply the worker produced for a command that has already been given up
    // on - after a restart, say - must not be credited to the current one.
    if (event.requestId !== undefined && Number(event.requestId) !== currentRequestId) return
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
      finishCommand(false)
      return
    }
    if (event.type !== "result") return
    lastResult = event.data
    var operation = String(event.operation || currentOperation)
    if (operation === "state") {
      var payload = event.data || ({})
      presets = Array.isArray(payload.presets) ? payload.presets : []
      presetGroups = Array.isArray(payload.groups) ? payload.groups : []
      statusMessage = presets.length === 0 ? "No presets saved yet" : "Ready"
    } else if (operation === "list") {
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
    finishCommand(true)
    changed()
  }

  Process {
    id: backend
    property string stderrText: ""
    property bool hadStructuredError: false

    stdinEnabled: true

    // Merged into the inherited environment; a bytecode cache outside the
    // plugin tree is worth about a fifth of every command's wall time. When no
    // cache directory can be resolved, fall back to writing none at all rather
    // than letting Python write one into the watched checkout.
    environment: root.bytecodeCacheDir === ""
      ? ({ "PYTHONDONTWRITEBYTECODE": "1" })
      : ({ "PYTHONPYCACHEPREFIX": root.bytecodeCacheDir })

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
      var wasReady = root.workerReady
      root.workerReady = false
      root.currentRequestId = 0
      if (root.busy) {
        // The worker died mid-command. Report it as this command's failure
        // rather than silently dropping the request.
        root.failCurrentCommand(
          backend.stderrText || "Backend exited with status " + exitCode
        )
      }
      if (!wasReady) {
        // It never finished starting, so the queue would spin restarting it.
        root.workerFailedToStart = true
        root.commandQueue = []
        if (root.errorMessage === "") {
          root.errorMessage = backend.stderrText || "Workspace Presets backend could not start"
          root.statusMessage = root.errorMessage
        }
        return
      }
      // A clean exit is the worker's own idle timeout; the next command
      // starts it again.
      Qt.callLater(root.startNext)
    }
  }
}
