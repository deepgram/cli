"""Performance timing utilities for deepctl."""

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import local
from typing import Any

from rich.console import Console

console = Console()

# Thread-local storage for timing data
_timing_data = local()


@dataclass
class TimingEntry:
    """Represents a single timing measurement."""

    name: str
    start_time: float
    end_time: float | None = None
    duration: float | None = None
    parent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def finish(self) -> float:
        """Mark this timing entry as finished and calculate duration."""
        if self.end_time is None:
            self.end_time = time.perf_counter()
            self.duration = self.end_time - self.start_time
        return self.duration or 0.0


class TimingCollector:
    """Collects and manages timing measurements."""

    def __init__(self) -> None:
        self.entries: dict[str, TimingEntry] = {}
        self.stack: list[str] = []
        self.enabled = False

    def enable(self) -> None:
        """Enable timing collection."""
        self.enabled = True

    def disable(self) -> None:
        """Disable timing collection."""
        self.enabled = False

    def start_timing(
        self, name: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Start timing for a named operation."""
        if not self.enabled:
            return

        parent = self.stack[-1] if self.stack else None
        entry = TimingEntry(
            name=name,
            start_time=time.perf_counter(),
            parent=parent,
            metadata=metadata or {},
        )
        self.entries[name] = entry
        self.stack.append(name)

    def end_timing(self, name: str) -> float | None:
        """End timing for a named operation."""
        if not self.enabled or name not in self.entries:
            return None

        entry = self.entries[name]
        duration = entry.finish()

        # Remove from stack if it's the current operation
        if self.stack and self.stack[-1] == name:
            self.stack.pop()

        return duration

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of all timing measurements."""
        if not self.enabled:
            return {}

        total_time = 0.0
        timings = {}

        for name, entry in self.entries.items():
            if entry.duration is not None:
                timings[name] = {
                    "duration_ms": round(entry.duration * 1000, 2),
                    "duration_s": round(entry.duration, 4),
                    "parent": entry.parent,
                    "metadata": entry.metadata,
                }
                if entry.parent is None:  # Top-level timing
                    total_time += entry.duration

        return {
            "total_time_ms": round(total_time * 1000, 2),
            "total_time_s": round(total_time, 4),
            "timings": timings,
        }

    def print_summary(self, detailed: bool = False) -> None:
        """Print a formatted summary of timing measurements."""
        if not self.enabled:
            return

        summary = self.get_summary()
        if not summary or not summary["timings"]:
            return

        console.print("\n[bold cyan]⏱️  Performance Timing Summary[/bold cyan]")
        console.print(
            f"[dim]Total execution time: {summary['total_time_ms']}ms ({summary['total_time_s']}s)[/dim]"
        )
        console.print()

        # Sort by duration (descending)
        sorted_timings = sorted(
            summary["timings"].items(),
            key=lambda x: x[1]["duration_ms"],
            reverse=True,
        )

        if detailed:
            # Show all timings with hierarchy
            for name, timing in sorted_timings:
                duration_ms = timing["duration_ms"]
                parent = timing.get("parent")
                metadata = timing.get("metadata", {})

                # Calculate percentage of total time
                if summary["total_time_ms"] > 0:
                    percentage = (duration_ms / summary["total_time_ms"]) * 100
                    percentage_str = f"({percentage:.1f}%)"
                else:
                    percentage_str = ""

                # Format the timing line
                if parent:
                    console.print(
                        f"  └─ [yellow]{name}[/yellow]: {duration_ms}ms {percentage_str}"
                    )
                else:
                    console.print(
                        f"[green]{name}[/green]: {duration_ms}ms {percentage_str}"
                    )

                # Show metadata if present
                if metadata:
                    for key, value in metadata.items():
                        console.print(f"     [dim]{key}: {value}[/dim]")
        else:
            # Show only top-level timings
            for name, timing in sorted_timings:
                if not timing.get("parent"):
                    duration_ms = timing["duration_ms"]

                    # Calculate percentage of total time
                    if summary["total_time_ms"] > 0:
                        percentage = (
                            duration_ms / summary["total_time_ms"]
                        ) * 100
                        percentage_str = f"({percentage:.1f}%)"
                    else:
                        percentage_str = ""

                    console.print(
                        f"[green]{name}[/green]: {duration_ms}ms {percentage_str}"
                    )


def get_timing_collector() -> TimingCollector:
    """Get the thread-local timing collector."""
    if not hasattr(_timing_data, "collector"):
        _timing_data.collector = TimingCollector()
    collector: TimingCollector = _timing_data.collector
    return collector


@contextmanager
def TimingContext(
    name: str, metadata: dict[str, Any] | None = None
) -> Generator[None, None, None]:
    """Context manager for timing operations."""
    collector = get_timing_collector()
    collector.start_timing(name, metadata)
    try:
        yield
    finally:
        collector.end_timing(name)


def enable_timing() -> None:
    """Enable performance timing collection."""
    collector = get_timing_collector()
    collector.enable()


def disable_timing() -> None:
    """Disable performance timing collection."""
    collector = get_timing_collector()
    collector.disable()


def get_timing_summary() -> dict[str, Any]:
    """Get the current timing summary."""
    collector = get_timing_collector()
    return collector.get_summary()


def print_timing_summary(detailed: bool = False) -> None:
    """Print the current timing summary."""
    collector = get_timing_collector()
    collector.print_summary(detailed)


def is_timing_enabled() -> bool:
    """Check if timing is currently enabled."""
    collector = get_timing_collector()
    return collector.enabled
