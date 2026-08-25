# Workspace Presets for Omarchy

Save the application windows on a Hyprland workspace as a named preset, then cold-load that preset onto the current workspace later. Workspace Presets launches missing applications, tracks the new windows, and rebuilds the saved layout instead of assuming the windows are already open.

This is a native Omarchy Quattro plugin: the bar widget and management panel run in `omarchy-shell`, while a bundled Python standard-library backend handles capture, validation, and restore orchestration. It does not install loose scripts, patch Omarchy menus, edit Hyprland configuration, run sudo, or use an install hook.

## What it restores

- Application window count and identity
- Dwindle trees and split ratios
- Master orientation, master/stack membership, and master factor
- Scrolling columns, column membership, widths, and tape position
- Monocle ordering and final focus
- Floating/tiled state and floating geometry
- Window groups, member order, active member, and lock state
- Fullscreen/maximized, pseudotile, pinning, and static tags
- Duplicate windows with the same class, tracked as independent slots

Application-owned state is outside the compositor's control and is not restored. That includes browser tabs, open documents, unsaved editor buffers, and terminal processes. Apps may restore some of that themselves through their own session support.

## Requirements

- Omarchy 4.0 or newer
- Hyprland 0.56 or newer
- Python 3
- `uwsm-app` and `gtk-launch` (included in a normal Omarchy installation)

The panel reports a clear compatibility error instead of attempting a partial restore when these requirements are not met. Version 1 supports normal workspaces using Hyprland's built-in `dwindle`, `master`, `scrolling`, or `monocle` layouts.

## Install

Review-first installation is recommended because all Omarchy shell plugins run as user code inside `omarchy-shell`:

```bash
omarchy plugin add https://github.com/blakestarling/omarchy-workspace-presets.git
omarchy plugin enable blakestarling.workspace-presets right
```

For the normal interactive one-command install:

```bash
omarchy plugin add https://github.com/blakestarling/omarchy-workspace-presets.git --enable
```

For a non-interactive installation where you have already reviewed the repository:

```bash
omarchy plugin add https://github.com/blakestarling/omarchy-workspace-presets.git --enable --yes
```

The plugin appears in the right section of the built-in bar. Left-click its workspace icon to open the preset manager; middle-click refreshes the list.

### Update, disable, and remove

```bash
omarchy plugin update blakestarling.workspace-presets
omarchy plugin disable blakestarling.workspace-presets
omarchy plugin enable blakestarling.workspace-presets right
omarchy plugin remove blakestarling.workspace-presets
```

Removing the plugin intentionally leaves presets in place. To delete that data too, first remove the plugin, then run:

```bash
rm -- ~/.config/omarchy-workspace-presets/presets.json ~/.config/omarchy-workspace-presets/presets.lock
```

If `XDG_CONFIG_HOME` is set, the data directory is `$XDG_CONFIG_HOME/omarchy-workspace-presets` instead.

## Use

### Save

1. Arrange the current workspace.
2. Open Workspace Presets from the bar.
3. Enter a unique name and choose **Save**.
4. If every window maps unambiguously to an installed desktop entry, the preset is immediately loadable.
5. Otherwise, choose **Set up** and select a suggested desktop entry, enter a `.desktop` ID, or provide a custom argv JSON array such as `["foot"]`.

A preset that needs launcher setup is saved as an explicit draft. It cannot be loaded until every window has a launch recipe; there are no `TODO` launch commands and unresolved windows are never silently skipped.

### Load

1. Choose **Load** on a ready preset.
2. Review how many current windows will close.
3. If matching windows exist on other workspaces, choose whether to launch new instances or move those existing windows. Nothing is moved silently.
4. Confirm the replacement.

Workspace Presets validates all launchers before closing anything. It then sends normal close requests to current-workspace applications and waits. If an application refuses to close—for example, because it is showing an unsaved-changes dialog—the restore stops and never force-kills it.

After the workspace is clear, the backend launches each saved slot through `uwsm-app`, waits for a newly created matching Hyprland stable ID, makes the windows temporarily floating, and rebuilds the saved layout deterministically. Groups and compositor state are restored last. A launch timeout is reported as a failure, never as a successful partial restore.

### Manage

- **Rename** changes the display name while preserving the preset's stable ID.
- **Overwrite** captures the current workspace into the selected preset after confirmation.
- **Delete** removes one preset after confirmation.
- **Refresh** reloads preset data from disk.

Preset names are trimmed, non-empty, and case-insensitively unique.

## Optional shell IPC

The widget exposes the standard Omarchy shell panel actions:

```bash
omarchy-shell shell toggle blakestarling.workspace-presets
omarchy-shell blakestarling.workspace-presets refresh
omarchy-shell blakestarling.workspace-presets save "Coding"
```

Starting a load over IPC still opens the panel for destructive confirmation:

```bash
omarchy-shell blakestarling.workspace-presets load PRESET_UUID
```

## Data and security

Presets are stored as schema-versioned JSON at:

```text
${XDG_CONFIG_HOME:-~/.config}/omarchy-workspace-presets/presets.json
```

Writes use an advisory lock, a same-directory temporary file, `fsync`, and atomic replacement. The data and lock files are mode `0600`.

Desktop launchers store only the desktop entry ID, so application updates can change their underlying `Exec` line without making the preset stale. Custom launchers are stored as argv arrays and are never evaluated through a shell. Only configure a custom command you trust: it runs as your user when that preset loads.

## Troubleshooting

Check compatibility and inspect saved presets without loading anything:

```bash
python3 -B ~/.config/omarchy/plugins/blakestarling.workspace-presets/backend/main.py capabilities
python3 -B ~/.config/omarchy/plugins/blakestarling.workspace-presets/backend/main.py list
```

Validate the installed manifest:

```bash
omarchy plugin validate ~/.config/omarchy/plugins/blakestarling.workspace-presets
```

If the widget does not appear after enabling it:

```bash
omarchy-shell shell rescanPlugins
omarchy bar move blakestarling.workspace-presets --section right
```

Common restore failures:

- **Active workspace changed:** return to the workspace named in the confirmation and start the load again. Nothing is closed when this guard trips.
- **Desktop entry no longer exists:** open **Set up** and select the replacement entry.
- **No new window appeared:** the app may be single-instance or need a custom `--new-window` command. Retry and choose **Move existing**, or configure a custom argv launcher.
- **Application did not close:** respond to its save/discard dialog, then load again.
- **Unsupported layout or special workspace:** switch the active normal workspace to one of the four supported built-in layouts before saving/loading.
- **Floating window moved after a monitor change:** exact pixels are used only when work-area size and scale match; otherwise geometry is normalized and clamped to the current monitor.

Hyprland 0.56 does not expose pseudotile state in its JSON client data. During capture the backend briefly forces pseudotile on and off, compares goal geometry, and immediately returns the window to its detected state. A state that produces no geometry difference is treated as visually equivalent.

## Development

Clone and validate:

```bash
git clone https://github.com/blakestarling/omarchy-workspace-presets.git
cd omarchy-workspace-presets
omarchy plugin validate .
PYTHONPATH=backend python3 -m unittest discover -s tests -v
python3 -m compileall -q backend
```

The backend's stdout is a newline-delimited JSON protocol. Commands emit `progress`, `result`, or structured `error` objects so the long-lived QML service never has to infer success from human-readable text.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the live-workspace test matrix and release checklist.

## License

MIT © Blake Starling. See [LICENSE](LICENSE).
