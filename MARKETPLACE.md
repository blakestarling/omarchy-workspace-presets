# Omarchy Plugin Marketplace submission

## Listing

- **Name:** Workspace Presets
- **Repository:** `https://github.com/blakestarling/omarchy-workspace-presets`
- **Plugin ID:** `blakestarling.workspace-presets`
- **Category:** Productivity
- **Tags:** `hyprland`, `quickshell`, `workspaces`
- **License:** MIT
- **Minimum Omarchy:** 4.0
- **Preview:** `assets/marketplace-preview.png`

## Short description

Save exact Hyprland workspace layouts, then cold-launch individual presets or complete multi-workspace preset groups.

## Long description

Workspace Presets is a native Omarchy bar widget and management panel for capturing, renaming, overwriting, deleting, and loading workspace presets. Presets can be assembled into named groups, assigned to numbered workspaces, launched together, and optionally restored once at session startup. Unlike layout scripts that only move windows which are already open, it resolves every saved window to a desktop entry or explicit argv command, launches missing applications through UWSM, tracks each newly created Hyprland stable ID, and reconstructs Dwindle, Master, Scrolling, or Monocle layouts. It also restores floating geometry, Hyprland window groups, fullscreen/maximized, pseudotile, pinning, tags, and focus.

Loads are deliberately guarded: launchers and compatibility are validated before any close request, the user confirms replacement, matching windows on other workspaces are never moved without permission, and applications are never force-killed.

## Reviewer notes

- The root `manifest.json` declares both a service and a bar widget.
- The Python backend uses only the standard library and remains inside the plugin checkout.
- Installation has no hooks, sudo, package installation, config editing, or files outside the XDG preset-data directory.
- Launch commands are stored as argv arrays and shell-quoted with `shlex.join` before
  Hyprland's `exec_cmd` runs them under `/bin/sh -c`. Preset content is never concatenated
  into a command string, and `tests/test_security.py` pins the quoting.
- Preset data survives plugin removal by design and can be purged with the documented non-recursive command.

## Submission checklist

1. Push `main` to the public repository above and create the `v1.0.0` release tag.
2. From a clean checkout, run `omarchy plugin validate .` and the tests documented in `CONTRIBUTING.md`.
3. Confirm `assets/marketplace-preview.png` reflects the released panel.
4. Open the [Omarchy Plugins submission form](https://github.com/HANCORE-linux/omarchy-plugin-marketplace/issues/new?template=submit-plugin.yml).
5. Submit the repository URL, **Productivity** category, and the tags listed above. The marketplace validates the current public commit before maintainer approval.
