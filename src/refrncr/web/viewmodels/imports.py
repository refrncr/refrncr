"""Pure presentation state for the record-import wizard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ImportState(Protocol):
    imported_count: int | None
    mapping_ready: bool
    validation_valid: bool


class ImportSource(Protocol):
    kind: str
    worksheets: tuple[str, ...]
    headers: tuple[str, ...]


@dataclass(frozen=True)
class WizardStep:
    number: int
    label: str
    done: bool
    reachable: bool


@dataclass(frozen=True)
class WizardStatus:
    success: bool
    automatic: bool
    steps: tuple[WizardStep, ...]
    back_step: int


def import_wizard_status(
    state: ImportState | None, source: ImportSource | None, step: int
) -> WizardStatus:
    """Describe completed and reachable wizard steps without web dependencies."""
    success = state is not None and state.imported_count is not None
    automatic = source is not None and (source.kind == "csv" or len(source.worksheets) == 1)
    completed = {
        1: state is not None,
        2: bool(source and source.headers),
        3: bool(state and state.mapping_ready),
        4: bool(state and state.validation_valid),
    }
    labels = ("Upload file", "Select worksheet", "Map columns", "Preview & import")
    steps = tuple(
        WizardStep(
            number=number,
            label=label,
            done=success or completed[number],
            reachable=bool(
                state
                and not success
                and number != step
                and (completed[number] or number == 3 and source and source.headers)
                and not (number == 2 and automatic)
            ),
        )
        for number, label in enumerate(labels, 1)
    )
    return WizardStatus(
        success=success,
        automatic=automatic,
        steps=steps,
        back_step=3 if step == 4 else 1 if automatic else step - 1,
    )
