# Security policy

Workspace Presets runs as the current user inside the Omarchy shell plugin trust model. Report a vulnerability privately through GitHub Security Advisories for `blakestarling/omarchy-workspace-presets` rather than opening a public issue.

The plugin does not request elevated privileges, install packages, or patch desktop configuration. Desktop launchers are referenced by ID, and custom launchers are validated JSON argv arrays.

Launching is the one place preset content reaches an interpreter. Hyprland's `exec_cmd` accepts a single string and runs it under `/bin/sh -c`, and there is no argv-based launch path in its Lua API, so every launcher is shell-quoted with `shlex.join` first. Preset content is never concatenated into a command string. Values interpolated into Hyprland's Lua IPC are quoted as Lua literals, and layout names, workspace selectors, window selectors, and tags are checked against allowlists.

Window titles, window classes, and captured command lines are written by whatever application owns the window, so they are treated as untrusted: they are stripped of markup and control characters before appearing in any message, because shell chrome outside this plugin may render text as auto-detected rich text.

Preset files are written atomically with mode `0600` into a `0700` directory, under a lock opened with `O_NOFOLLOW`. A preset is proven internally consistent during preflight, before any window is closed.
