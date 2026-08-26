# Workspace Presets for Omarchy

Save the application windows on a Hyprland workspace as a named preset, then cold-load that preset later. Combine presets into groups assigned to numbered workspaces and launch a complete multi-workspace setup in one action—or automatically once when the Hyprland session starts. Workspace Presets launches missing applications, tracks the new windows, and rebuilds the saved layout instead of assuming the windows are already open.

![Workspace Presets group manager showing presets assigned to workspaces 1 and 0](assets/workspace-presets-groups.png)

This is a native Omarchy Quattro plugin: the bar widget and management panel run in `omarchy-shell`, while a bundled Python standard-library backend handles capture, validation, and restore orchestration.

## What it restores

- Application window count and identity
- Dwindle trees and split ratios
- Master orientation, master/stack membership, and master factor
- Scrolling columns, column membership, column widths, row sizes, and tape position
- Monocle ordering and final focus
- Floating/tiled state and floating geometry
- Window groups, member order, active member, and lock state
- Fullscreen/maximized, pinning, and static tags
- Duplicate windows with the same class, tracked as independent slots
- Omarchy shell panels discovered from their plugin manifests
- Explicit terminal programs and their working directories, including Herdr and Omarchy's Docker View

## Limitations

Application-owned state is outside the compositor's control and is not restored. That includes browser tabs, open documents, unsaved editor buffers, and the internal state of terminal programs. When a supported terminal was launched with an explicit program, the plugin saves and reruns that outer terminal command; the program itself remains responsible for restoring its session.

## Requirements

- Omarchy 4.0 or newer
- Hyprland 0.56 or newer
- Python 3
- `uwsm-app` and `gtk-launch` (included in a normal Omarchy installation)

The panel reports a clear compatibility error instead of attempting a partial restore when these requirements are not met. Version 1 supports normal workspaces using Hyprland's built-in `dwindle`, `master`, `scrolling`, or `monocle` layouts.

## Install

```bash
omarchy plugin add https://github.com/blakestarling/omarchy-workspace-presets.git --enable
```

The plugin appears in the built-in bar. Left-click its workspace icon to open the preset manager; middle-click refreshes the list.

### Optional keyboard shortcut

`SUPER + ALT + P` is easy to remember as “Presets” and is unused by Omarchy's default bindings as of Omarchy 4.0. Add this line to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + P", "Workspace Presets", "omarchy-shell shell toggle blakestarling.workspace-presets")
```

Hyprland normally reloads the file automatically. You can apply and validate it explicitly with:

```bash
hyprctl reload
hyprctl configerrors
```

If you have added personal bindings, check `omarchy menu keybindings --print` first. If the shortcut is already used, choose another key combination or call `hl.unbind("SUPER + ALT + P")` before the new `o.bind` and intentionally replace the old action.

### Update

```bash
omarchy plugin update blakestarling.workspace-presets
```

### Disable

```bash
omarchy plugin disable blakestarling.workspace-presets
```

### Remove

```bash
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

A preset that needs launcher setup is saved as an explicit draft. It cannot be loaded until every window has a launch recipe. Installed Omarchy panel plugins are matched by their manifest name and relaunched through `omarchy-shell`; existing drafts are rechecked automatically when the service starts.

Omarchy web apps are matched from the URL in their desktop entry and Chrome's URL-derived window class. This supports entries with human-readable filenames such as `Google Messages.desktop` and `WhatsApp.desktop` without mistaking them for an ordinary browser window. Existing drafts are repaired automatically when a unique web-app match becomes available.

Foreground programs are detected automatically in Foot, Alacritty, Kitty, Ghostty, and WezTerm, whether the program was supplied when the terminal opened or started manually from its shell. The plugin reads the terminal's controlling TTY and foreground process group, then preserves that program's exact argv and working directory. For example, Herdr is saved as a terminal invocation ending in `-e herdr`, while Docker View retains `-e omarchy-launch-docker-tui` when that wrapper is still present in the running process tree.

This detection deliberately does not infer commands from shell history. Idle shells restore as normal terminals, while pipelines, ambiguous process trees, SSH sessions, and tmux or zellij sessions fall back to the terminal's normal launcher instead of saving a misleading partial command. Captured commands recreate the program but cannot preserve unsaved in-memory application state. Overwrite presets captured by an older plugin version to replace their generic terminal launchers with the richer recipe.

### Load

1. Choose **Load** on a ready preset.
2. If the current workspace is empty, loading starts immediately after validation. Matching windows on other workspaces are left untouched and new instances are launched.
3. Otherwise, review how many current windows will close. If matching windows exist on other workspaces, you can choose whether to launch new instances or move those existing windows. Nothing is moved silently.
4. Confirm the replacement.

Press **Escape** at any time to close the Workspace Presets panel and cancel a pending load confirmation.

The panel closes automatically when a confirmed preset or preset-group load begins, leaving the workspace unobstructed while applications launch.

Workspace Presets validates all launchers before closing anything. It then sends normal close requests to current-workspace applications and waits. If an application refuses to close—for example, because it is showing an unsaved-changes dialog—the restore stops and never force-kills it.

After the workspace is clear, the backend launches saved applications through `uwsm-app` concurrently, routes them silently without stealing focus, and tracks each newly created matching Hyprland stable ID. Once identified, each window is temporarily removed from the tiled layout; the saved layout is then rebuilt deterministically with explicit saved-order anchors. This avoids attaching a forced-floating launch rule to the window while still preventing compositor arrival order from changing the final layout. Windows with duplicate or overlapping classes launch in separate waves so they cannot be assigned to the wrong slot. Groups and compositor state are restored last. A launch timeout is reported as a failure, never as a successful partial restore.

### Manage

- **Rename** changes the display name while preserving the preset's stable ID.
- **Overwrite** captures the current workspace into the selected preset after confirmation.
- **Delete** removes one preset after confirmation.
- **Refresh** reloads preset data from disk.

Preset names are trimmed, non-empty, and case-insensitively unique.

### Preset groups

1. Under **Preset groups**, enter a unique group name and choose **Create group**.
2. For each preset you want in the group, enter a number-row workspace key from **0 through 9** and choose **Assign**. As in Omarchy's default bindings, `0` targets workspace 10. A group allows one preset per workspace and one assignment per preset. Unsaved workspace edits remain in place while other assignments or group settings are updated.
3. Choose **Launch group**. The plugin validates every preset, launcher, and target workspace before it changes anything.
4. If all target workspaces are empty, launch begins immediately. Otherwise, one confirmation shows the total windows that will receive normal close requests.

Group loads launch new application instances instead of moving matches from unrelated workspaces. All target workspaces are cleared first, then unrelated applications for every preset start concurrently and are routed directly to their assigned workspace with a silent one-shot Hyprland rule. Exact layout reconstruction still requires a brief final pass over each target workspace because Hyprland's layout dispatcher operates on the active workspace. Focus returns to the workspace and window that were active when the group launch began. If a group or any target workspace changes after confirmation, the operation stops before closing anything.

Groups can be renamed, reassigned, and deleted without deleting their presets. A preset cannot be deleted while a group references it; remove that assignment first.

### Launch a group on startup

Choose **Launch on startup** on a complete group. Only one group can hold this setting, so enabling another transfers it. The plugin runs the selected group once when its service first starts in a new Hyprland session. A session-scoped guard prevents an `omarchy-shell` reload or plugin rescan from launching the group again. Enabling the setting does not immediately launch the group; it takes effect on the next Hyprland session.

Enable **Confirm before startup launch** on the selected startup group if you do not want it to launch unconditionally. At the next login, the plugin opens its panel with a preflight summary and waits for **Launch group**. Choosing **Cancel**, pressing **Escape**, or closing the panel skips the group for that session; shell reloads will not prompt again until the next login.

Startup restore is intentionally equivalent to a confirmed group launch: assigned workspaces are replaced with normal close requests and applications are never force-killed. If a launcher or preset becomes invalid, startup restore reports the error rather than partially skipping it.

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

Group loads follow the same preflight and confirmation flow:

```bash
omarchy-shell blakestarling.workspace-presets loadGroup GROUP_UUID
```

## Data and security

Presets, preset groups, assignments, the startup selection, and its confirmation preference are stored as schema-versioned JSON at:

```text
${XDG_CONFIG_HOME:-~/.config}/omarchy-workspace-presets/presets.json
```

Writes use an advisory lock, a same-directory temporary file, `fsync`, and atomic replacement. The data and lock files are mode `0600`.

Desktop launchers store only the desktop entry ID, so application updates can change their underlying `Exec` line without making the preset stale. Custom launchers are stored as argv arrays and are never evaluated through a shell. Explicit terminal commands and their working directories are captured automatically; this can include command-line arguments, so avoid putting secrets directly in terminal command arguments. Only configure commands you trust: they run as your user when that preset loads.

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
omarchy bar move blakestarling.workspace-presets --section left
```

Common restore failures:

- **Active workspace changed:** return to the workspace named in the confirmation and start the load again. Nothing is closed when this guard trips.
- **Desktop entry no longer exists:** open **Set up** and select the replacement entry.
- **No new window appeared:** the app may be single-instance or need a custom `--new-window` command. Retry and choose **Move existing**, or configure a custom argv launcher.
- **Application did not close:** respond to its save/discard dialog, then load again.
- **Startup group did not run after a shell reload:** this is intentional; startup groups run at most once per Hyprland session. Log out and back in to test the next-session behavior.
- **Preset is used by a group:** remove that preset's group assignment before deleting it.
- **Unsupported layout or special workspace:** switch the active normal workspace to one of the four supported built-in layouts before saving/loading.
- **Floating window moved after a monitor change:** exact pixels are used only when work-area size and scale match; otherwise geometry is normalized and clamped to the current monitor.

Hyprland 0.56 does not expose pseudotile state through read-only window IPC. Workspace Presets therefore leaves live windows untouched during capture and records pseudotile as disabled. This avoids changing the workspace merely by saving it; pseudotile capture can be added when Hyprland exposes that state safely.

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
