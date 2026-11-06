/**
 * The player as an explicit state machine, with no DOM, so it can be tested on its own.
 *
 * It decides; useLecturePlayer carries it out. It knows nothing about which page is on screen:
 * looking at a page and listening to one are separate, so turning pages never interrupts audio.
 *
 * `page` mode plays a page's ranges in order, jumping over whatever lies between them, and
 * stops after the last one. `lecture` mode plays the recording straight through.
 */

export interface PlaybackRange {
  start: number
  end: number
}

export type PlaybackStatus = 'stopped' | 'playing' | 'paused'

export interface PlaybackState {
  mode: 'lecture' | 'page'
  status: PlaybackStatus
  /** The page being listened to, in page mode. */
  page: number | null
  ranges: PlaybackRange[]
  rangeIndex: number
  /** A seek we asked for and the audio hasn't finished. Time reports until then are stale. */
  seekingTo: number | null
}

export type PlaybackEvent =
  | { type: 'playPage'; page: number; ranges: PlaybackRange[] }
  | { type: 'playLecture'; from?: number }
  | { type: 'seek'; time: number }
  | { type: 'jumpToRange'; index: number }
  | { type: 'toggle' }
  | { type: 'time'; time: number }
  | { type: 'seeked' }
  | { type: 'playing' }
  | { type: 'paused' }
  | { type: 'ended' }

export type PlaybackCommand = { type: 'seek'; time: number } | { type: 'play' } | { type: 'pause' }

export interface Transition {
  state: PlaybackState
  commands: PlaybackCommand[]
}

/** How close to a range's end counts as its end, since time reports are not continuous. */
const END_TOLERANCE = 0.05
/** A report this close to the requested time means the seek has landed. */
const SEEK_TOLERANCE = 0.5

export const initialPlayback: PlaybackState = {
  mode: 'lecture',
  status: 'stopped',
  page: null,
  ranges: [],
  rangeIndex: 0,
  seekingTo: null,
}

/** Put overlapping or out-of-order ranges into a clean, playable list. */
export function normalizeRanges(ranges: PlaybackRange[]): PlaybackRange[] {
  const merged: PlaybackRange[] = []
  for (const range of [...ranges].sort((a, b) => a.start - b.start)) {
    if (range.end <= range.start) continue
    const last = merged[merged.length - 1]
    if (last && range.start <= last.end) last.end = Math.max(last.end, range.end)
    else merged.push({ ...range })
  }
  return merged
}

function rangeIndexAt(ranges: PlaybackRange[], time: number) {
  return ranges.findIndex((range) => time >= range.start && time < range.end)
}

function advance(state: PlaybackState): Transition {
  const next = state.rangeIndex + 1
  if (next < state.ranges.length) {
    const time = state.ranges[next].start
    return {
      state: { ...state, rangeIndex: next, seekingTo: time },
      commands: [{ type: 'seek', time }],
    }
  }
  // The last range is over: stop here, rather than run on into another page's speech.
  return { state: { ...state, status: 'stopped', seekingTo: null }, commands: [{ type: 'pause' }] }
}

function onTime(state: PlaybackState, time: number): Transition {
  if (state.seekingTo !== null) {
    if (Math.abs(time - state.seekingTo) > SEEK_TOLERANCE) return { state, commands: [] }
    state = { ...state, seekingTo: null }
  }
  if (state.mode !== 'page' || state.status !== 'playing') return { state, commands: [] }
  const range = state.ranges[state.rangeIndex]
  if (range && time >= range.end - END_TOLERANCE) return advance(state)
  return { state, commands: [] }
}

export function transition(state: PlaybackState, event: PlaybackEvent): Transition {
  switch (event.type) {
    case 'playPage': {
      const ranges = normalizeRanges(event.ranges)
      if (ranges.length === 0) return { state, commands: [] }
      const time = ranges[0].start
      return {
        state: {
          mode: 'page',
          status: 'playing',
          page: event.page,
          ranges,
          rangeIndex: 0,
          seekingTo: time,
        },
        commands: [{ type: 'seek', time }, { type: 'play' }],
      }
    }
    case 'playLecture': {
      const next: PlaybackState = {
        ...initialPlayback,
        status: 'playing',
        seekingTo: event.from ?? null,
      }
      return {
        state: next,
        commands:
          event.from === undefined
            ? [{ type: 'play' }]
            : [{ type: 'seek', time: event.from }, { type: 'play' }],
      }
    }
    case 'seek': {
      if (state.mode === 'page') {
        const index = rangeIndexAt(state.ranges, event.time)
        if (index >= 0) {
          return {
            state: { ...state, rangeIndex: index, seekingTo: event.time },
            commands: [{ type: 'seek', time: event.time }],
          }
        }
        // Outside the page: the listener wants that moment, so stop enforcing the ranges.
        return {
          state: { ...initialPlayback, status: state.status, seekingTo: event.time },
          commands: [{ type: 'seek', time: event.time }],
        }
      }
      return {
        state: { ...state, seekingTo: event.time },
        commands: [{ type: 'seek', time: event.time }],
      }
    }
    case 'jumpToRange': {
      const range = state.ranges[event.index]
      if (state.mode !== 'page' || !range) return { state, commands: [] }
      return {
        state: { ...state, rangeIndex: event.index, status: 'playing', seekingTo: range.start },
        commands: [{ type: 'seek', time: range.start }, { type: 'play' }],
      }
    }
    case 'toggle': {
      if (state.status === 'playing') return { state, commands: [{ type: 'pause' }] }
      // Finished the page: playing again means from the top, not from past its last range.
      if (state.mode === 'page' && state.status === 'stopped' && state.ranges.length > 0) {
        const time = state.ranges[0].start
        return {
          state: { ...state, status: 'playing', rangeIndex: 0, seekingTo: time },
          commands: [{ type: 'seek', time }, { type: 'play' }],
        }
      }
      return { state, commands: [{ type: 'play' }] }
    }
    case 'time':
      return onTime(state, event.time)
    case 'seeked':
      return { state: { ...state, seekingTo: null }, commands: [] }
    case 'playing':
      return { state: { ...state, status: 'playing' }, commands: [] }
    case 'paused':
      // Pausing is how a finished page stops, and that must not read as a mere pause.
      return state.status === 'stopped'
        ? { state, commands: [] }
        : { state: { ...state, status: 'paused' }, commands: [] }
    case 'ended':
      return { state: { ...state, status: 'stopped', seekingTo: null }, commands: [] }
  }
}
