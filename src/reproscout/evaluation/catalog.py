"""Loading and validating the built-in offline evaluation set."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from .models import EvaluationSet


class EvaluationSetError(ValueError):
    """Raised when benchmark data is missing, malformed, or unsafe."""


def load_evaluation_set() -> EvaluationSet:
    """Load the packaged, versioned evaluation set without executing fixtures."""

    try:
        raw = (
            files("reproscout.evaluation")
            .joinpath("evaluation_set.json")
            .read_text(encoding="utf-8")
        )
        value: Any = json.loads(raw)
        return EvaluationSet.model_validate(value)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise EvaluationSetError("The packaged evaluation set is invalid.") from exc
