"""Typed application errors. Each maps to a user-facing message plus a logged detail."""
from __future__ import annotations


class ArchPilotError(Exception):
    """Base class: message is safe to show to a non-technical user."""

    def user_message(self) -> str:
        return str(self) or self.__class__.__name__


class FreeCADNotFound(ArchPilotError):
    def __init__(self, message: str = "FreeCAD was not found on this computer."):
        super().__init__(message)

    def user_message(self) -> str:
        return (f"{self}\n\nInstall FreeCAD 1.x from freecad.org, then set the path to "
                f"freecadcmd.exe in Settings and Connections.")


class FreeCADOperationFailed(ArchPilotError):
    def __init__(self, message: str, *, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class FreeCADTimeout(ArchPilotError):
    pass


class InvalidSpecification(ArchPilotError):
    pass


class MissingRate(ArchPilotError):
    pass


class UnsupportedOperation(ArchPilotError):
    pass


class StorageError(ArchPilotError):
    pass


class AIProviderError(ArchPilotError):
    pass
