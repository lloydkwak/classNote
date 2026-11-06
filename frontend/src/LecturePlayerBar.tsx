import type { KeyboardEvent, MouseEvent } from 'react'
import type { PageAudio, PageAudioEntry } from './api'
import { formatTime } from './format'
import type { PlaybackState } from './playback'

const KEY_STEP_SECONDS = 5

function Timeline(props: {
  pageAudio: PageAudio | null
  page: number
  time: number
  duration: number
  onSeek: (time: number) => void
}) {
  const { duration, time, onSeek } = props
  const ranges = props.pageAudio?.pages.flatMap((entry) =>
    entry.audio_ranges.map((range) => ({ ...range, page: entry.page })),
  )
  const percent = (value: number) => `${(value / duration) * 100}%`

  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    onSeek(((event.clientX - box.left) / box.width) * duration)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, number> = {
      ArrowLeft: Math.max(time - KEY_STEP_SECONDS, 0),
      ArrowRight: Math.min(time + KEY_STEP_SECONDS, duration),
      Home: 0,
      End: duration,
    }
    if (event.key in moves) {
      event.preventDefault()
      event.stopPropagation() // These keys otherwise turn the page.
      onSeek(moves[event.key])
    }
  }

  return (
    <div
      className="timeline"
      role="slider"
      tabIndex={0}
      aria-label="강의 재생 위치"
      aria-valuemin={0}
      aria-valuemax={Math.round(duration)}
      aria-valuenow={Math.round(time)}
      aria-valuetext={`${formatTime(time)} / ${formatTime(duration)}`}
      onClick={duration > 0 ? onClick : undefined}
      onKeyDown={duration > 0 ? onKeyDown : undefined}
    >
      {duration > 0 &&
        ranges?.map((range) => (
          <span
            key={`${range.page}-${range.start}`}
            className={`timeline-range${range.page === props.page ? ' current' : ''}`}
            style={{ left: percent(range.start), width: percent(range.end - range.start) }}
            title={`${range.page}쪽 ${formatTime(range.start)}–${formatTime(range.end)}`}
          />
        ))}
      {duration > 0 && <span className="timeline-head" style={{ left: percent(time) }} />}
    </div>
  )
}

/** Plays the page on screen or the whole lecture, and shows where the page was explained. */
export function LecturePlayerBar(props: {
  page: number
  entry?: PageAudioEntry
  pageAudio: PageAudio | null
  playback: PlaybackState
  time: number
  duration: number
  rate: number
  savedTime: number
  onPlayPage: () => void
  onPlayLecture: (from?: number) => void
  onToggle: () => void
  onSeek: (time: number) => void
  onJumpToRange: (index: number) => void
  onCycleRate: () => void
  onGoToPage: (page: number) => void
}) {
  const { page, entry, playback, time } = props
  const ranges = entry?.audio_ranges ?? []
  const playingThisPage = playback.mode === 'page' && playback.page === page
  const isPlaying = playback.status === 'playing'
  const canResume = props.savedTime > 0 && playback.status === 'stopped' && time === 0

  const status = () => {
    if (playback.mode === 'page' && playback.page !== null) {
      if (playback.status === 'stopped') return `${playback.page}쪽 강의를 끝까지 들었습니다`
      const position = `구간 ${playback.rangeIndex + 1}/${playback.ranges.length}`
      return `${playback.page}쪽 강의 ${isPlaying ? '재생 중' : '일시정지'} · ${position}`
    }
    if (playback.status === 'stopped') return ''
    return `전체 강의 ${isPlaying ? '재생 중' : '일시정지'}`
  }

  return (
    <footer className="player">
      <div className="player-row">
        <button
          className="primary"
          disabled={ranges.length === 0}
          onClick={props.onPlayPage}
          title={ranges.length === 0 ? '이 페이지에 매칭된 강의 구간이 없습니다' : undefined}
        >
          ▶ 이 페이지 강의 듣기
          {entry && entry.total_duration > 0 && (
            <span className="player-duration"> ({formatTime(entry.total_duration)})</span>
          )}
        </button>
        <button onClick={() => props.onPlayLecture()}>전체 강의 재생</button>
        {canResume && (
          <button onClick={() => props.onPlayLecture(props.savedTime)}>
            이어 듣기 ({formatTime(props.savedTime)})
          </button>
        )}
        <button
          className="player-toggle"
          aria-label={isPlaying ? '일시정지' : '재생'}
          onClick={props.onToggle}
        >
          {isPlaying ? '❚❚' : '▶'}
        </button>
        <span className="player-time">
          {formatTime(time)} / {formatTime(props.duration)}
        </span>
        <button aria-label={`재생 속도 ${props.rate}배`} onClick={props.onCycleRate}>
          {props.rate}×
        </button>
        <span className="player-status" role="status">
          {status()}
          {playback.mode === 'page' && playback.page !== null && playback.page !== page && (
            <button className="link" onClick={() => props.onGoToPage(playback.page as number)}>
              {playback.page}쪽으로 이동
            </button>
          )}
        </span>
      </div>
      <Timeline
        pageAudio={props.pageAudio}
        page={page}
        time={time}
        duration={props.duration}
        onSeek={props.onSeek}
      />
      <div className="player-ranges">
        {props.pageAudio && ranges.length === 0 && (
          <span className="muted">이 페이지에 매칭된 강의 구간이 없습니다.</span>
        )}
        {ranges.map((range, index) => {
          const current = playingThisPage && playback.rangeIndex === index
          return (
            <button
              key={range.start}
              className={`range-chip${current ? ' current' : ''}${
                range.confidence < 0.2 ? ' unsure' : ''
              }`}
              aria-current={current ? 'true' : undefined}
              title={`정렬 신뢰도 ${Math.round(range.confidence * 100)}%`}
              onClick={() => {
                if (playingThisPage) props.onJumpToRange(index)
                else props.onPlayLecture(range.start)
              }}
            >
              {range.reason === 'revisit' ? '다시 설명' : '처음 설명'} {formatTime(range.start)}–
              {formatTime(range.end)}
            </button>
          )
        })}
      </div>
    </footer>
  )
}
