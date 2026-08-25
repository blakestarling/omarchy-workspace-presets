# Omarchy Plugin Marketplace submission

## Listing

- **Name:** Workspace Presets
- **Repository:** `https://github.com/blakestarling/omarchy-workspace-presets`
- **Plugin ID:** `blakestarling.workspace-presets`
- **Category:** System / Productivity
- **License:** MIT
- **Minimum Omarchy:** 4.0
- **Preview:** `assets/marketplace-preview.png`

## Short description

Save application windows and exact Hyprland workspace layouts as named presets, then cold-launch and rebuild them on the current workspace.

## Long description

Workspace Presets is a native Omarchy bar widget and management panel for capturing, renaming, overwriting, deleting, and loading workspace presets. Unlike layout scripts that only move windows which are already open, it resolves every saved window to a desktop entry or explicit argv command, launches missing applications through UWSM, tracks each newly created Hyprland stable ID, and reconstructs Dwindle, Master, Scrolling, or Monocle layouts. It also restores floating geometry, groups, fullscreen/maximized, pseudotile, pinning, tags, and focus.

Loads are deliberately guarded: launchers and compatibility are validated before any close request, the user confirms replacement, matching windows on other workspaces are never moved without permission, and applications are never force-killed.

## Reviewer notes

- The root `manifest.json` declares both a service and a bar widget.
- The Python backend uses only the standard library and remains inside the plugin checkout.
- Installation has no hooks, sudo, package installation, config editing, or files outside the XDG preset-data directory.
- Custom launch commands are argv arrays and never pass through a shell.
- Preset data survives plugin removal by design and can be purged with the documented non-recursive command.
