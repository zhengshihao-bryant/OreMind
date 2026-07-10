"""Common schema definitions for the OreMind system."""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Document:
    """Represents a raw ingested document."""
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None


@dataclass
class Chunk:
    """A segment of a document after splitting."""
    id: str
    document_id: str
    content: str
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryRequest:
    """Input to the retrieval system."""
    query: str
    top_k: int = 10
    filters: Optional[dict[str, Any]] = None


@dataclass
class RetrievalResult:
    """A single retrieved item with relevance information."""
    chunk: Chunk
    score: float
    rank: int


@dataclass
class QueryResponse:
    """Output of the retrieval pipeline."""
    query: str
    results: list[RetrievalResult] = field(default_factory=list)
    total_hits: int = 0
