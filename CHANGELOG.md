# Changelog

All notable changes to Workspace Presets are documented here.

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
