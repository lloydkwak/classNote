"""Group transcript segments into windows of roughly equal length."""

from app.models import Segment, Window

DEFAULT_WINDOW_SECONDS = 30.0


def build_windows(
    segments: list[Segment], window_seconds: float = DEFAULT_WINDOW_SECONDS
) -> list[Window]:
    """Close a window once it reaches `window_seconds`. Segments are never cut."""
    windows: list[Window] = []
    current: list[Segment] = []

    def close_window() -> None:
        windows.append(
            Window(
                window_id=len(windows),
                start=current[0].start,
                end=current[-1].end,
                text=" ".join(segment.text for segment in current),
            )
        )
        current.clear()

    for segment in segments:
        current.append(segment)
        if current[-1].end - current[0].start >= window_seconds:
            close_window()
    if current:
        close_window()
    return windows
