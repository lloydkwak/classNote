// Run with `npm test`, which uses Node's built-in runner and needs no extra packages.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  initialPlayback,
  normalizeRanges,
  transition,
  type PlaybackCommand,
  type PlaybackEvent,
  type PlaybackState,
} from './playback.ts'

const RANGES = [
  { start: 10, end: 20 },
  { start: 50, end: 60 },
]

function run(state: PlaybackState, ...events: PlaybackEvent[]) {
  let commands: PlaybackCommand[] = []
  for (const event of events) {
    const result = transition(state, event)
    state = result.state
    commands = result.commands
  }
  return { state, commands }
}

const playing = () =>
  run(initialPlayback, { type: 'playPage', page: 3, ranges: RANGES }, { type: 'seeked' }).state

test('playing a page seeks to its first range and plays', () => {
  const { state, commands } = run(initialPlayback, { type: 'playPage', page: 3, ranges: RANGES })
  assert.deepEqual(commands, [{ type: 'seek', time: 10 }, { type: 'play' }])
  assert.equal(state.mode, 'page')
  assert.equal(state.page, 3)
  assert.equal(state.status, 'playing')
})

test('a page with no ranges does nothing', () => {
  const { state, commands } = run(initialPlayback, { type: 'playPage', page: 3, ranges: [] })
  assert.deepEqual(commands, [])
  assert.equal(state, initialPlayback)
})

test('reaching the end of a range jumps to the next one', () => {
  const { state, commands } = run(playing(), { type: 'time', time: 20 })
  assert.deepEqual(commands, [{ type: 'seek', time: 50 }])
  assert.equal(state.rangeIndex, 1)
  assert.equal(state.status, 'playing')
})

test('time inside a range does nothing', () => {
  const { commands } = run(playing(), { type: 'time', time: 15 })
  assert.deepEqual(commands, [])
})

test('the end of the last range stops, and does not run on', () => {
  const { state, commands } = run(
    playing(),
    { type: 'time', time: 20 },
    { type: 'seeked' },
    { type: 'time', time: 60 },
  )
  assert.deepEqual(commands, [{ type: 'pause' }])
  assert.equal(state.status, 'stopped')
})

test('the pause that stops a finished page does not read as a pause', () => {
  const { state } = run(
    playing(),
    { type: 'time', time: 20 },
    { type: 'seeked' },
    { type: 'time', time: 60 },
    { type: 'paused' },
  )
  assert.equal(state.status, 'stopped')
})

test('a stale time report during a seek does not skip a range', () => {
  // After jumping to the second range, the audio can still report the old position.
  const { state, commands } = run(
    playing(),
    { type: 'time', time: 20 },
    { type: 'time', time: 20.1 },
    { type: 'time', time: 20.2 },
  )
  assert.deepEqual(commands, [])
  assert.equal(state.rangeIndex, 1)
  assert.equal(state.seekingTo, 50)
})

test('a time report near the target also ends the seek', () => {
  const { state } = run(playing(), { type: 'time', time: 20 }, { type: 'time', time: 50.1 })
  assert.equal(state.seekingTo, null)
})

test('a time past a range end after a slow tick still advances once', () => {
  const { state } = run(playing(), { type: 'time', time: 23 })
  assert.equal(state.rangeIndex, 1)
})

test('seeking inside another range of the page moves to that range', () => {
  const { state, commands } = run(playing(), { type: 'seek', time: 55 })
  assert.deepEqual(commands, [{ type: 'seek', time: 55 }])
  assert.equal(state.mode, 'page')
  assert.equal(state.rangeIndex, 1)
})

test('after seeking inside a later range, the page still ends at its last range', () => {
  const { state, commands } = run(
    playing(),
    { type: 'seek', time: 55 },
    { type: 'seeked' },
    { type: 'time', time: 60 },
  )
  assert.deepEqual(commands, [{ type: 'pause' }])
  assert.equal(state.status, 'stopped')
})

test('seeking outside the page leaves page mode and keeps playing', () => {
  const { state, commands } = run(playing(), { type: 'seek', time: 30 })
  assert.deepEqual(commands, [{ type: 'seek', time: 30 }])
  assert.equal(state.mode, 'lecture')
  assert.equal(state.page, null)
  assert.equal(state.status, 'playing')
  assert.deepEqual(run(state, { type: 'seeked' }, { type: 'time', time: 60 }).commands, [])
})

test('seeking while paused stays paused', () => {
  const { state } = run(playing(), { type: 'paused' }, { type: 'seek', time: 12 })
  assert.equal(state.status, 'paused')
})

test('a paused page does not advance', () => {
  const { state, commands } = run(playing(), { type: 'paused' }, { type: 'time', time: 20 })
  assert.deepEqual(commands, [])
  assert.equal(state.rangeIndex, 0)
})

test('resuming a paused page continues where it was', () => {
  const { state, commands } = run(playing(), { type: 'paused' }, { type: 'toggle' })
  assert.deepEqual(commands, [{ type: 'play' }])
  assert.equal(state.rangeIndex, 0)
})

test('playing a finished page again starts from its first range', () => {
  const { state, commands } = run(
    playing(),
    { type: 'time', time: 20 },
    { type: 'seeked' },
    { type: 'time', time: 60 },
    { type: 'toggle' },
  )
  assert.deepEqual(commands, [{ type: 'seek', time: 10 }, { type: 'play' }])
  assert.equal(state.rangeIndex, 0)
})

test('jumping to a revisit plays from there', () => {
  const { state, commands } = run(playing(), { type: 'jumpToRange', index: 1 })
  assert.deepEqual(commands, [{ type: 'seek', time: 50 }, { type: 'play' }])
  assert.equal(state.rangeIndex, 1)
})

test('jumping to a range that does not exist does nothing', () => {
  assert.deepEqual(run(playing(), { type: 'jumpToRange', index: 9 }).commands, [])
})

test('lecture mode never jumps at a range boundary', () => {
  const { state } = run(initialPlayback, { type: 'playLecture' }, { type: 'playing' })
  assert.deepEqual(run(state, { type: 'time', time: 20 }).commands, [])
})

test('lecture mode can resume from a saved time', () => {
  const { commands } = run(initialPlayback, { type: 'playLecture', from: 42 })
  assert.deepEqual(commands, [{ type: 'seek', time: 42 }, { type: 'play' }])
})

test('switching from a page to the lecture drops the ranges', () => {
  const { state } = run(playing(), { type: 'playLecture' })
  assert.equal(state.mode, 'lecture')
  assert.deepEqual(state.ranges, [])
})

test('playing another page replaces the first', () => {
  const { state, commands } = run(playing(), {
    type: 'playPage',
    page: 5,
    ranges: [{ start: 100, end: 110 }],
  })
  assert.equal(state.page, 5)
  assert.deepEqual(commands, [{ type: 'seek', time: 100 }, { type: 'play' }])
})

test('the recording ending stops the player', () => {
  const { state } = run(playing(), { type: 'ended' })
  assert.equal(state.status, 'stopped')
})

test('ranges are sorted, merged, and cleaned', () => {
  assert.deepEqual(
    normalizeRanges([
      { start: 50, end: 60 },
      { start: 10, end: 20 },
      { start: 15, end: 30 },
      { start: 70, end: 70 },
    ]),
    [
      { start: 10, end: 30 },
      { start: 50, end: 60 },
    ],
  )
})

test('normalizing does not change the input', () => {
  const input = [
    { start: 10, end: 20 },
    { start: 15, end: 30 },
  ]
  normalizeRanges(input)
  assert.equal(input[0].end, 20)
})
