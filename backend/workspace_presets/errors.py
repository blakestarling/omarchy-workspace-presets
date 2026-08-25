"""Typed failures surfaced to the QML service."""


class WorkspacePresetsError(RuntimeError):
    code = "workspace-presets-error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class ValidationError(WorkspacePresetsError):
    code = "validation-error"


class HyprlandError(WorkspacePresetsError):
    code = "hyprland-error"


class UnsupportedError(WorkspacePresetsError):
    code = "unsupported"


class LaunchError(WorkspacePresetsError):
    code = "launch-error"


class RestoreError(WorkspacePresetsError):
    code = "restore-error"
