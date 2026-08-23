from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Category = Literal["bug", "security", "maintainability", "test"]
Severity = Literal["critical", "high", "medium", "low"]


@dataclass(slots=True)
class ReviewFinding:
    id: str
    file: str
    start_line: int
    end_line: int
    category: Category
    severity: Severity
    title: str
    description: str
    evidence: str
    suggestion: str
    confidence: float
    used_memory_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewFinding":
        required = {
            "id",
            "file",
            "start_line",
            "end_line",
            "category",
            "severity",
            "title",
            "description",
            "evidence",
            "suggestion",
            "confidence",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise ValueError(f"review finding is missing fields: {', '.join(missing)}")

        finding = cls(
            id=str(value["id"]),
            file=str(value["file"]),
            start_line=int(value["start_line"]),
            end_line=int(value["end_line"]),
            category=value["category"],
            severity=value["severity"],
            title=str(value["title"]),
            description=str(value["description"]),
            evidence=str(value["evidence"]),
            suggestion=str(value["suggestion"]),
            confidence=float(value["confidence"]),
            used_memory_ids=[str(item) for item in value.get("used_memory_ids", [])],
        )
        finding.validate()
        return finding

    def validate(self) -> None:
        if self.category not in {"bug", "security", "maintainability", "test"}:
            raise ValueError(f"invalid category: {self.category}")
        if self.severity not in {"critical", "high", "medium", "low"}:
            raise ValueError(f"invalid severity: {self.severity}")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid line range")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
            "confidence": self.confidence,
            "used_memory_ids": self.used_memory_ids,
        }


@dataclass(slots=True)
class ReviewResult:
    reviews: list[ReviewFinding]
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewResult":
        if "reviews" not in value or not isinstance(value["reviews"], list):
            raise ValueError("review result must contain a reviews list")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        return cls(
            reviews=[ReviewFinding.from_dict(item) for item in value["reviews"]],
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviews": [review.to_dict() for review in self.reviews],
            "metadata": self.metadata,
        }