"""Diagnostic checks surfaced via ``homekit diagnose``."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    name: str
    passed: bool
    details: str
