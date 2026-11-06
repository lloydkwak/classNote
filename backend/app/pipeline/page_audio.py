"""Work out which parts of the recording explain each page, independent of any notes.

This needs only the alignment, so it costs no LLM call, changing the note density never
touches it, and a job whose notes failed can still play each page's lecture.
"""

from app.models import Alignment, AudioRange, PageAudio, PageAudioEntry

# Windows on one page this close together are one visit: the gap is a short pause.
MERGE_GAP_SECONDS = 3.0
# Padding so the first and last sounds of a range aren't clipped. Never crosses into a neighbor.
PADDING_SECONDS = 0.25
FIRST_VISIT = "first_visit"
REVISIT = "revisit"


def _merge_windows(alignment: Alignment) -> list[tuple[int, list]]:
    """Group consecutive windows on the same page, as (page, windows).

    An off-topic window or another page's window always ends the group, so a range never
    plays speech that belongs elsewhere.
    """
    groups: list[tuple[int, list]] = []
    previous_page = None
    for window in sorted(alignment.windows, key=lambda item: item.start):
        if window.page is None or window.end <= window.start:
            previous_page = None
            continue
        if (
            groups
            and window.page == previous_page
            and window.start - groups[-1][1][-1].end <= MERGE_GAP_SECONDS
        ):
            groups[-1][1].append(window)
        else:
            groups.append((window.page, [window]))
        previous_page = window.page
    return groups


def _weighted_confidence(windows: list) -> float:
    total = sum(window.end - window.start for window in windows)
    return sum(window.confidence * (window.end - window.start) for window in windows) / total


def build_page_audio(alignment: Alignment, duration: float | None = None) -> PageAudio:
    """Return every page's ranges in time order. A page with no speech has an empty list.

    `duration` is the recording's length, when known. Times are always in the original
    recording's clock, so a range can be played by seeking in the source audio.
    """
    groups = _merge_windows(alignment)
    limit = duration if duration is not None else max(
        (window.end for window in alignment.windows), default=0.0
    )
    # Every window counts as a neighbor, so padding can't reach into off-topic speech either.
    ordered = sorted(alignment.windows, key=lambda item: item.start)
    positions = {window.window_id: index for index, window in enumerate(ordered)}
    ranges_by_page: dict[int, list[AudioRange]] = {page: [] for page in alignment.pages}
    for page, windows in groups:
        first, last = positions[windows[0].window_id], positions[windows[-1].window_id]
        # Two padded neighbors meet halfway between them.
        earliest = (ordered[first - 1].end + windows[0].start) / 2 if first else 0.0
        latest = (windows[-1].end + ordered[last + 1].start) / 2 if last + 1 < len(ordered) else limit
        start = max(windows[0].start - PADDING_SECONDS, earliest, 0.0)
        end = min(windows[-1].end + PADDING_SECONDS, latest, limit)
        if end <= start:  # Only a recording shorter than the transcript could do this.
            continue
        visits = ranges_by_page.setdefault(page, [])
        visits.append(
            AudioRange(
                start=round(start, 3),
                end=round(end, 3),
                confidence=round(_weighted_confidence(windows), 3),
                source_window_ids=[f"w{window.window_id}" for window in windows],
                reason=REVISIT if visits else FIRST_VISIT,
            )
        )
    entries = [
        PageAudioEntry(
            page=page,
            audio_ranges=ranges,
            primary_start=ranges[0].start if ranges else None,
            total_duration=round(sum(item.end - item.start for item in ranges), 3),
        )
        for page, ranges in sorted(ranges_by_page.items())
    ]
    unmatched = [
        window for window in alignment.windows if window.page is None and window.end > window.start
    ]
    return PageAudio(
        pages=entries,
        duration=limit,
        unmatched_seconds=round(sum(window.end - window.start for window in unmatched), 3),
    )
