"""Workspace Presets backend."""

import re

# A desktop entry id in the form the freedesktop scan produces: an entry's file
# name with any directory separators folded to "-". Shared with the store so a
# launcher can only be saved with an id the scan could itself have resolved,
# rather than any string that happens to end in ".desktop".
SAFE_DESKTOP_ID = re.compile(r"^[A-Za-z0-9_.+ -]+\.desktop$")

SCHEMA_VERSION = 1
SUPPORTED_LAYOUTS = ("dwindle", "master", "scrolling", "monocle")
PLUGIN_ID = "blakestarling.workspace-presets"
VERSION = "1.9.0"
