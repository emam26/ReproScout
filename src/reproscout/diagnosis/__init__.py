"""Bounded autonomous-diagnosis foundation."""

from .engine import DiagnosisEngineError, DiagnosisSession, deterministic_diagnosis
from .models import (
    Diagnosis,
    DiagnosisAction,
    DiagnosisHypothesis,
    DiagnosisLimits,
    DiagnosisResult,
    DiagnosisRisk,
    DiagnosisStatus,
)
from .parser import InvalidDiagnosisError, parse_diagnosis_decision
from .policy import DiagnosisPolicyError, RepeatedDiagnosisError, validate_diagnosis
from .prompt import build_diagnosis_request, diagnosis_prompt_text

__all__ = [
    "Diagnosis",
    "DiagnosisAction",
    "DiagnosisEngineError",
    "DiagnosisHypothesis",
    "DiagnosisLimits",
    "DiagnosisPolicyError",
    "DiagnosisResult",
    "DiagnosisRisk",
    "DiagnosisSession",
    "DiagnosisStatus",
    "InvalidDiagnosisError",
    "RepeatedDiagnosisError",
    "build_diagnosis_request",
    "deterministic_diagnosis",
    "diagnosis_prompt_text",
    "parse_diagnosis_decision",
    "validate_diagnosis",
]
