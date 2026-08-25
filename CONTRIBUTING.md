# Contributing

## Local checks

```bash
omarchy plugin validate .
PYTHONPATH=backend python3 -m unittest discover -s tests -v
python3 -m compileall -q backend
```

Keep the backend dependency-free. Do not add install hooks, sudo calls, writes to `/usr/share/omarchy`, edits to `~/.config/hypr`, or shell evaluation of preset data.

## Live Hyprland matrix

Run destructive restore checks only on a dedicated empty workspace. Use applications with no unsaved data.

- Cold-load a preset when none of its applications are open.
- Save and restore two or more windows with the same class.
- Round-trip Dwindle orientation/tree depth and non-default ratios.
- Round-trip Master orientation, multiple masters, ordering, and master factor.
- Round-trip Scrolling column widths and multiple windows per column.
- Round-trip Monocle ordering and final focus.
- Round-trip floating geometry, a locked group, fullscreen, maximized, pseudotile, and pin state.
- Load after changing monitor resolution or scale and verify all floating windows remain visible.
- Keep a matching window on another workspace and verify both conflict choices.
- Verify unresolved launchers cannot load.
- Verify a launch timeout produces a visible failure.
- Verify an application that refuses to close is never force-killed.
- Disable, re-enable, update, and remove the plugin; verify preset data survives.

## Release checklist

1. Update `manifest.json`, `CHANGELOG.md`, and the backend `VERSION` together.
2. Run every local check and the live matrix on the current supported Omarchy release.
3. Capture a current panel screenshot for the marketplace listing.
4. Tag the release as `vMAJOR.MINOR.PATCH`.
5. Confirm the README installation URL works from a clean user account.
