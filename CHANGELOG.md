# Changelog

All notable changes to Workspace Presets are documented here.

## Unreleased

- Keep newly matched windows provisional while their compositor-visible surface state is still changing, and rebind a slot when an updater or splash is replaced by the application's real window.
- Defer temporary floating until each group workspace is actively being finalized, restore saved floating modes if finalization fails, and continue finalizing independent targets before reporting the failure.
- Synchronize a group target through an exact window before reading its active context, closing the remaining focus-publication race during cold application startup.

## 1.9.0 - 2026-08-26

Performance work across the backend, the shell service, and the login path. Behaviour is unchanged: `list`, `groups`, `details`, `capabilities`, `desktop-entries`, `resolve-launchers` and `preflight` all produce byte-identical output, including the preflight confirmation token.

- Talk to Hyprland over its IPC socket instead of spawning `hyprctl` per call - 9.2 ms against 0.12 ms measured, and an eight-window dwindle restore issues 69 of those calls in its finalize stage alone. hyprctl's own request grammar is reused, so responses are byte-identical.
- Serve every command from one worker that exits after two idle minutes, instead of starting an interpreter and importing the backend for each. That setup was about 72 ms of the ~90 ms a `list` took. Startup sequence 766 ms to 213 ms; panel refresh 190 ms to 1.4 ms; preflight 459 ms to 24.5 ms.
- Wait on the compositor's `openwindow`, `closewindow` and `activewindowv2` events rather than re-asking every 120 ms. Window detection drops from 123 ms to 69 ms. A missed or unavailable event falls back to exactly the old polling.
- Parse desktop entries with a line reader rather than ConfigParser, which was the slowest step of a capture or preflight at 45 ms and 91% of everything it allocated. Same 108 entries, no value differences, 11 ms and 17 KiB.
- Memoise the capability probe. Every preflight opened by re-running `omarchy version`, a shell script costing 80 ms, to re-answer a question that cannot change during a session. The panel's explicit recheck still probes.
- Reuse the parsed store while the file is unchanged, and stop deep-copying the whole document to build summaries from it. Loading a five-workspace group read, validated and copied the entire store eleven times.
- Apply every launcher repair in one write. A six-window draft rewrote and fsynced the whole store six times, on every service start.
- Answer the presets and groups lists together with one `state` command and one read, so the panel opens with one request instead of two.
- Relocate Python's bytecode cache with `PYTHONPYCACHEPREFIX` rather than disabling it with `-B`. The watched checkout stays just as inert and every command loses about a fifth of its time.
- Read the process table once per capture rather than once per terminal window, scan panel-plugin manifests once per preflight rather than once per window slot, and drop `dataclasses` for the three small classes that used it.
- Build the management and confirmation panels on first use rather than when the bar starts, and index group assignments once per refresh instead of searching every group per row.

Login path:

- Run the startup launch second rather than fifth, after the launcher repair pass it depends on and ahead of the work that only fills a panel nobody has opened. Time from service start to the launch request: 537 ms to 110 ms.
- Wait for monitor geometry to settle before launching. Saved geometry is normalized against the work area the bar reserves, and the bar is this plugin's own host, so at login that reservation may not have landed yet. Stability is the test rather than a non-zero reservation, so a session with no bar does not wait out the timeout.
- Allow 30 seconds for applications to appear at login rather than the interactive 12. They start against a cold page cache while the rest of the session is still coming up, and a miss is reported as a failed restore, never a partial one.
- Release the once-per-session startup guard when the attempt failed before checking anything, so a session that simply was not ready yet stays retryable. A failure during the load itself keeps the guard: windows may already have been closed, and a retry must not launch anything twice.

## 1.8.3 - 2026-08-26

Security hardening from a full audit of the backend, the Hyprland Lua bridge, and the QML surface.

- Strip markup and control characters from window classes and titles before they appear in any message. These are set by the application that owns the window - a browser title is a remote page's own title element - and bar tooltips are drawn by shell chrome that renders auto-detected rich text.
- Open the preset lock with `O_NOFOLLOW` and adjust it by descriptor, so a symlink planted in the data directory can no longer redirect its permission fix onto another file. Restrict the data directory itself on every access.
- Quote Lua literals with Lua's own escaping instead of JSON's. Control characters previously produced `\uXXXX`, which Hyprland cannot compile, failing a restore with an opaque IPC error.
- Refuse saved layout names outside the supported set, and non-finite geometry, before either reaches Hyprland's Lua evaluator.
- Prove a preset is internally consistent during preflight, while its windows still exist, instead of failing part-way through a restore that has already closed them.
- Route windows by numeric workspace ID rather than by name, so a workspace named `+2`, `empty`, or `previous` can no longer be reinterpreted as a relative or special target.
- Keep the once-per-session startup marker in the private per-user runtime directory and refuse to run without one, rather than falling back to a predictable path in world-writable `/tmp`.
- Reject null bytes in launcher arguments and bound argv to the same limits the capture path already applies.
- Correct `SECURITY.md` and the marketplace reviewer notes: launch commands do reach `/bin/sh -c` through Hyprland's `exec_cmd`, and are made safe by `shlex.join` quoting rather than by avoiding a shell. Document that terminal command lines are captured verbatim.

## 1.8.2 - 2026-08-26

- Bind single-preset loads to the exact preflighted preset, workspace, and stable window IDs, and never close windows that appear after preflight.
- Render all QML text as plain text so application and desktop-entry metadata cannot trigger rich-text resource loading.

## 1.8.1 - 2026-08-26

- Synchronize every target workspace through an exact saved window before rebuilding groups or tiling, preventing startup-time focus publication races from leaving non-final workspaces floating.
- Extend the focus barrier timeout for a busy login compositor while retaining immediate completion whenever Hyprland is already ready.

## 1.8.0 - 2026-08-26

- Move manual preset and preset-group load warnings into a focused confirmation panel that opens after the main panel closes.
- Reuse the dedicated confirmation panel for optional startup-group confirmation, with Escape, cancel, and outside-click dismissal.
- Keep safe loads frictionless: when no workspace replacement is required, loading still begins immediately without opening confirmation UI.

## 1.7.6 - 2026-08-26

- Accept Hyprland's hexadecimal stable window IDs when capturing layout metadata, preventing Scrolling presets from falling back to nondeterministic client enumeration order.
- Use hexadecimal stable IDs directly for window selectors and cover metadata capture with a regression test.

## 1.7.5 - 2026-08-26

- Make capture read-only by removing the pseudotile toggle probe that could visibly recalculate tiled windows while a preset was being saved.
- Capture and restore each window's secondary-axis size inside multi-window Scrolling columns.
- Save Scrolling tape position proportionally and restore its absolute leading edge after final focus, preventing focus-driven viewport drift.

## 1.7.4 - 2026-08-26

- Replay each Scrolling workspace topology in one compositor transaction so concurrently loaded preset-group workspaces cannot redirect one another's focus-dependent layout commands.
- Reorder singleton columns to the saved slot order before rebuilding multi-window columns.
- Preserve the user's focused window and pointer position around atomic Scrolling replay.

## 1.7.3 - 2026-08-26

- Synchronize focus before issuing layout-dependent replay commands so Hyprland operates on the intended stable window rather than the previously active node or scrolling column.
- Restore multi-window Scrolling columns reliably instead of leaving every window in a separate column.
- Apply the same focus barrier to Dwindle anchors and Master layout controls without adding fixed retry delays.

## 1.7.2 - 2026-08-26

- Remove the forced-floating launch rule that could reassert itself after a group advanced focus to another workspace.
- Route new windows silently without initial focus, then temporarily detach identified stable IDs before deterministic layout replay.
- Remove the delayed retry passes introduced in 1.7.1, restoring the faster finalization path.

## 1.7.1 - 2026-08-26

- Verify tiled window state after applications finish their late startup surface updates.
- Deterministically rebuild the tiled layout if an application reasserts floating after the initial restore pass.
- Report an explicit restore error instead of silently succeeding if a window cannot be kept tiled.

## 1.7.0 - 2026-08-26

- Add an optional confirmation toggle for the selected startup preset group.
- Open the panel at login with a validated group summary and Launch/Cancel choice when confirmation is enabled.
- Treat Cancel, Escape, or closing the panel as skipping the launch for the current session without prompting again after a shell reload.

## 1.6.6 - 2026-08-26

- Keep buttons and fields visually stable during backend work, using an invisible interaction shield for foreground operations instead of fading every control.
- Keep empty-state messages stable during refreshes instead of briefly hiding and reappearing.

## 1.6.5 - 2026-08-26

- Inset panel content so left and right `BorderSurface` strokes are no longer clipped by the scrolling viewport.
- Hide progress for background startup and refresh commands, and delay foreground progress by 300 ms to eliminate brief “Working…” flashes.

## 1.6.4 - 2026-08-26

- Keep automatic panel closing reliable when a plugin hot reload temporarily retains an older service instance.

## 1.6.3 - 2026-08-26

- Launch restored windows floating and without initial focus so concurrent arrival order cannot mutate the target tiling layout.
- Anchor every retiled window to the previous saved window, making Monocle, Master, and Scrolling replay order deterministic.

## 1.6.2 - 2026-08-26

- Resolve Omarchy web apps from their launch URLs and Chrome's URL-derived window classes.
- Support desktop-entry filenames containing spaces, including Google Messages and WhatsApp.
- Automatically repair existing draft presets with uniquely matched Omarchy web-app launchers.

## 1.6.1 - 2026-08-26

- Close the management panel automatically as soon as a confirmed preset or preset-group load begins.
- Document an unused, memorable Super+Alt+P shortcut for toggling the panel.

## 1.6.0 - 2026-08-26

- Detect programs started manually inside supported terminals by inspecting the controlling TTY's foreground process group.
- Preserve the foreground program's exact argv and working directory without depending on shell history.
- Keep idle shells, remote or multiplexer sessions, ambiguous terminal trees, and pipelines on the normal terminal fallback instead of guessing an unsafe partial command.

## 1.5.0 - 2026-08-26

- Launch unrelated saved applications concurrently instead of waiting for each application before starting the next one.
- Keep duplicate or overlapping window classes in ordered launch waves so stable-ID matching remains deterministic.
- Launch preset-group applications directly onto their assigned workspaces with Hyprland's one-shot silent workspace rules.
- Close all group targets first, populate every workspace in parallel, and limit workspace switching to the final exact-layout pass.
- Restore the user's original focused workspace and window after a group load, including failed launch attempts.

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
