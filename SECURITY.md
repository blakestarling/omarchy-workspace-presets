# Security policy

Workspace Presets runs as the current user inside the Omarchy shell plugin trust model. Report a vulnerability privately through GitHub Security Advisories for `blakestarling/omarchy-workspace-presets` rather than opening a public issue.

The plugin does not request elevated privileges, install packages, patch desktop configuration, or evaluate preset content through a shell. Desktop launchers are referenced by ID, and custom launchers are validated JSON argv arrays. Preset files are written atomically with mode `0600`.
