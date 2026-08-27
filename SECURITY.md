# Security policy

Workspace Presets runs as the current user inside the Omarchy shell plugin trust model. Report a vulnerability privately through GitHub Security Advisories for `blakestarling/omarchy-workspace-presets` rather than opening a public issue.

The plugin does not request elevated privileges, install packages, or patch desktop configuration. Desktop launchers are referenced by ID, and custom launchers are validated JSON argv arrays.

Launching is the one place preset content reaches an interpreter. Hyprland's `exec_cmd` accepts a single string and runs it under `/bin/sh -c`, and there is no argv-based launch path in its Lua API, so every launcher is shell-quoted with `shlex.join` first. Preset content is never concatenated into a command string. Values interpolated into Hyprland's Lua IPC are quoted as Lua literals, and layout names, workspace selectors, window selectors, and tags are checked against allowlists.

Desktop entries and Omarchy panel manifests are read from directories the user can write to, so each one is opened non-blocking and must be a regular file within a fixed size ceiling before it is parsed; a file that fails either check is skipped rather than allowed to stall or exhaust a scan. Symlinks are followed there on purpose, because packaged and dotfile-managed entries are commonly links.

Window titles, window classes, and captured command lines are written by whatever application owns the window, so they are treated as untrusted: they are stripped of markup and control characters before appearing in any message, because shell chrome outside this plugin may render text as auto-detected rich text.

Preset files are written atomically with mode `0600` into a `0700` directory, under a lock opened with `O_NOFOLLOW`. They are read back through a single `O_NOFOLLOW`, `O_NONBLOCK` descriptor that must refer to a regular file within a fixed size ceiling, so a symlink, FIFO, device node, or oversized file left at that path is refused rather than followed, waited on, or read into the long-lived service. A preset is proven internally consistent during preflight, before any window is closed.
