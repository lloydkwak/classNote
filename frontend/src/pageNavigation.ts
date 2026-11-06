import type { PageAudio } from './api'

/** UI 0 is Overview; original API page numbers remain 1..N. */
export function clampPage(page: number, sourceCount: number) {
  return Math.min(Math.max(page, 0), sourceCount || 1)
}
export function sourceSlideIndex(page: number) { return page === 0 ? null : page - 1 }
export function exportPdfPageNumber(page: number) { return page + 1 }

/** Follow only a currently active, sufficiently confident SOURCE slide interval. */
export function pageAtTime(pageAudio: PageAudio, time: number) {
  let latest: { page: number; start: number } | null = null
  for (const entry of pageAudio.pages) {
    for (const range of entry.audio_ranges) {
      if (range.start <= time && time < range.end && range.confidence >= .3 && (!latest || range.start > latest.start)) {
        latest = { page: entry.page, start: range.start }
      }
    }
  }
  return latest?.page ?? null
}
