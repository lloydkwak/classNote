"""Metrics use interval intersections, never labels at a window midpoint."""

from collections import Counter
from difflib import SequenceMatcher

from app.evaluation.schema import eligible
from app.pipeline.block_align import quote_in_transcript


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def overlap(first, second):
    return max(0.0, min(first.end, second.end) - max(first.start, second.start))


def alignment_metrics(alignment, dataset, allow_provisional=False, tolerance=5.0):
    windows = sorted(alignment.windows, key=lambda window: window.start)
    if any(window.start >= window.end for window in windows) or any(
        first.end > following.start for first, following in zip(windows, windows[1:])
    ):
        raise ValueError("Predicted intervals must have positive duration and cannot overlap")
    labels = [label for label in dataset.alignment_labels if eligible(label.review, allow_provisional)]
    exact = near = total = off_tp = off_pred = off_true = 0.0
    bins = [{"min": lower, "max": lower + .25, "seconds": 0.0, "error_seconds": 0.0}
            for lower in (0.0, .25, .5, .75)]
    for window in windows:
        for label in labels:
            seconds = overlap(window, label)
            if not seconds:
                continue
            correct = window.page in label.pages if label.pages else window.page is None
            close = (window.page is not None and any(abs(window.page - page) <= 1 for page in label.pages))
            exact += seconds * correct
            near += seconds * (correct or close)
            total += seconds
            off_tp += seconds * (not label.pages and window.page is None)
            off_pred += seconds * (window.page is None)
            off_true += seconds * (not label.pages)
            bucket = bins[min(3, max(0, int(window.confidence * 4)))]
            bucket["seconds"] += seconds
            bucket["error_seconds"] += seconds * (not correct)
    for bucket in bins:
        bucket["error_rate"] = ratio(bucket["error_seconds"], bucket["seconds"])
    transitions = [row for row in dataset.transitions if eligible(row.review, allow_provisional)]
    predictions = [(following.start, first.page, following.page)
                   for first, following in zip(windows, windows[1:])
                   if first.page != following.page and abs(first.end - following.start) < 1e-6]
    used = set()
    errors = []
    matched_revisits = matches = 0
    for transition in sorted(transitions, key=lambda row: row.time):
        candidates = [(abs(time - transition.time), index)
                      for index, (time, source, target) in enumerate(predictions)
                      if index not in used
                      and (source in transition.from_pages if transition.from_pages else source is None)
                      and (target in transition.to_pages if transition.to_pages else target is None)]
        if candidates:
            error, index = min(candidates)
            if error <= tolerance:
                used.add(index)
                matches += 1
                matched_revisits += transition.revisit
                errors.append(error)
    revisits = sum(transition.revisit for transition in transitions)
    first = next((window for window in windows if window.page is not None), None)
    ordered_labels = sorted(labels, key=lambda label: label.start)
    first_label = next((label for label in ordered_labels if label.pages), None)
    cursor = 0.0
    for label in ordered_labels:
        if label.start > cursor:
            break
        cursor = label.end
    first_known = first_label is not None and first is not None and cursor >= max(first.start, first_label.end)
    duration = max((window.end for window in windows), default=0)
    return {
        "exact_page_accuracy": ratio(exact, total), "within_one_page_accuracy": ratio(near, total),
        "evaluated_seconds": total, "label_count": len(labels),
        "verified_label_count": sum(label.review.status == "verified" for label in labels),
        "labeled_seconds": sum(label.end - label.start for label in labels),
        "recording_coverage": ratio(total, duration),
        "transition_accuracy": ratio(matches, len(transitions)),
        "transition_matches": matches, "transition_labels": len(transitions),
        "boundary_mae_seconds": ratio(sum(errors), len(errors)),
        "boundary_matched_count": len(errors), "boundary_tolerance_seconds": tolerance,
        "first_page_accuracy": float(first.page in first_label.pages) if first_known else None,
        "off_topic_precision": ratio(off_tp, off_pred), "off_topic_recall": ratio(off_tp, off_true),
        "revisit_accuracy": ratio(matched_revisits, revisits), "revisit_labels": revisits,
        "confidence_bins": bins,
    }


def notes_metrics(content, annotations, segments, dataset, allow_provisional=False):
    units = {unit.id: unit for unit in dataset.meaning_units if eligible(unit.review, allow_provisional)}
    items = {item.id: item for item in content.items}
    reviews = [match for match in dataset.matches
               if match.item_id in items and eligible(match.review, allow_provisional)]
    reviewed_units = {unit_id for match in reviews if match.relevant is True
                      for unit_id in match.unit_ids if unit_id in units}
    annotation_ids = [note.content_id for page in annotations.pages for note in page.notes] if annotations else []
    pdf_units = {unit_id for match in reviews if match.item_id in annotation_ids and match.relevant is True
                 for unit_id in match.unit_ids if unit_id in units}
    assessed = [match for match in reviews if match.relevant is not None]
    factual = [match for match in reviews if match.entailed is not None]
    transcript = " ".join(segment.text for segment in segments)
    suggestions = []
    for item in content.items:
        candidates = [{"unit_id": unit.id,
                       "text_similarity": SequenceMatcher(None, item.content.casefold(), unit.content.casefold()).ratio(),
                       "overlap_seconds": overlap(item, unit)}
                      for unit in dataset.meaning_units if overlap(item, unit) > 0]
        suggestions.append({"item_id": item.id, "reviewed": any(match.item_id == item.id for match in reviews),
                            "candidates": sorted(candidates, key=lambda row: -row["text_similarity"]),
                            "automatic_match_is_verified": False})
    ranges = [row for row in dataset.meaning_review_ranges if eligible(row.review, allow_provisional)]
    covered_units = {unit.id for unit in units.values()
                     if any(row.start <= unit.start and unit.end <= row.end for row in ranges)}
    return {
        "human_units": len(units), "reviewed_extracted_items": len(reviews), "extracted_items": len(items),
        "extraction_recall": ratio(len(reviewed_units & covered_units), len(covered_units)) if len(reviews) == len(items) else None,
        "extraction_recall_lower_bound": ratio(len(reviewed_units & covered_units), len(covered_units)),
        "recall_denominator_units": len(covered_units),
        "matching_review_complete": len(reviews) == len(items),
        "extraction_precision_reviewed": ratio(sum(match.relevant is True for match in assessed), len(assessed)),
        "semantic_factuality_reviewed": ratio(sum(match.entailed is True for match in factual), len(factual)),
        "semantic_factuality_review_count": len(factual),
        "quote_match_rate": ratio(sum(quote_in_transcript(item.source_quote, transcript) for item in items.values()), len(items)),
        "timed_quote_match_rate": ratio(sum(quote_in_transcript(item.source_quote,
            " ".join(segment.text for segment in segments if overlap(segment, item) > 0))
            for item in items.values()), len(items)),
        "retained_unit_ids": sorted(reviewed_units), "pdf_unit_ids": sorted(pdf_units),
        "important_pdf_recall": ratio(sum(units[unit_id].importance >= 4 for unit_id in pdf_units & covered_units),
                                       sum(units[unit_id].importance >= 4 for unit_id in covered_units)),
        "duplicate_reviews": [{"item_id": match.item_id, "duplicate_of": match.duplicate_of}
                              for match in reviews if match.duplicate_of],
        "repeated_annotation_ids": [identifier for identifier, count in Counter(annotation_ids).items()
                                    if identifier is not None and count > 1],
        "rejection_reasons": dict(Counter(record.reason for record in content.validation if record.status == "rejected")),
        "unavailable_extractions": [record.model_dump(exclude={"candidate"}) for record in content.validation if record.status == "unavailable"],
        "review_ranges_seconds": sum(row.end - row.start for row in ranges),
        "matching_suggestions": suggestions,
        "telemetry": getattr(content, "telemetry", {}),
        "span_diagnostics": getattr(content, "spans", []),
        "missing_spans": [span for span in getattr(content, "spans", []) if span.get("accepted") == 0],
        "unmatched_unit_ids": sorted(covered_units - reviewed_units),
        "unreviewed_item_ids": sorted(set(items) - {match.item_id for match in reviews}),
    }
