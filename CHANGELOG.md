# Changelog

All notable changes to Workspace Presets are documented here.

## 1.4.0 - 2026-08-26

- Capture explicit programs launched inside supported terminal emulators and replay the terminal's exact argv and working directory.
- Restore Omarchy terminal TUIs such as Herdr and Docker View through their original launch wrappers instead of reopening an empty terminal.
- Include captured terminal program names in preset search.

## 1.3.4 - 2026-08-25

- Map the user-facing workspace key 0 to Hyprland workspace 10, matching Omarchy's default number-row bindings.
- Reject literal Hyprland workspace ID 0 before dispatch and keep group progress labeled with the configured 0–9 key.

## 1.3.3 - 2026-08-25

- Allow preset groups to focus and load workspace 0 through the Hyprland dispatcher.
- Place the bar widget in the left section by default on new installations.

## 1.3.2 - 2026-08-25

- Keep Escape-to-close working after a save refresh destroys and recreates the focused form delegate.

## 1.3.1 - 2026-08-25

- Preserve in-progress workspace assignment edits when another preset or group mutation refreshes the panel.
- Allow workspace 0 in preset groups and constrain every group target to the supported 0–9 range in both the panel and backend.
- Return focus to workspace 0 after launching a preset group when it was the originally active workspace.

## 1.3.0 - 2026-08-25

- Split preset and preset-group management into focused tabs.
- Add contextual search across names, saved applications, layouts, assignments, and workspace numbers.
- Add Recent, Most used, Name, Recently updated, and size-based sorting for both tabs.
- Track successful preset and group launches with backward-compatible usage counts and last-used timestamps.
- Keep workspace-number fields synchronized with saved group assignments after edits and refreshes.

## 1.2.1 - 2026-08-25

- Recognize installed Omarchy panel plugins by their manifests and save a native shell-IPC launcher instead of requiring manual setup.
- Automatically repair existing unresolved presets when a uniquely matching Omarchy panel is installed.
- Validate panel launchers at restore time and summon them through the supported `omarchy-shell` interface.

## 1.2.0 - 2026-08-25

- Add named preset groups with per-preset numbered-workspace assignments.
- Create, rename, edit, delete, and launch groups from the native plugin panel.
- Preflight every group target before changing any workspace and reject stale confirmations.
- Optionally launch one selected group at session startup, guarded so shell reloads cannot launch it twice.
- Keep presets referenced by groups safe from accidental deletion.

## 1.1.1 - 2026-08-25

- Skip confirmation on an empty workspace even when matching windows exist elsewhere; load new instances and leave existing windows untouched.

## 1.1.0 - 2026-08-25

- Load immediately after preflight when the current workspace is empty and no matching windows exist elsewhere.
- Keep confirmation when loading would close, duplicate, or move an existing window.
- Close the panel with Escape from the main view, forms, and confirmation states.

## 1.0.1 - 2026-08-25

- Keep failed load errors visible instead of clearing them with an automatic list refresh.
- Expose the pending preflight and last structured result through the diagnostic IPC status.

## 1.0.0 - 2026-08-25

- Initial Omarchy Quattro service, bar widget, and management panel.
- Cold application launching with per-window stable-ID tracking.
- Capture/replay support for Dwindle, Master, Scrolling, and Monocle.
- Floating geometry, groups, fullscreen, pseudotile, pin, tags, and focus restoration.
- Draft presets with explicit desktop-entry or argv launcher resolution.
- Atomic schema-versioned storage and graceful close-only replacement safety.
- Hyprland 0.56 typed-Lua dispatch integration and guarded workspace confirmation.
- Stable-ID normalization for exact group membership capture.
