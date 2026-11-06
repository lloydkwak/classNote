import { useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { getAlignment, type Alignment, type AlignedWindow } from './api'
import { formatTime } from './format'

const HEIGHT = 420
const MARGIN = { top: 16, right: 92, bottom: 36, left: 48 }

function midpoint(window: AlignedWindow) {
  return (window.start + window.end) / 2
}

/** Whole-number ticks, at most about `count` of them, always including the first page. */
function pageTicks(max: number, count = 8) {
  const step = [1, 2, 5, 10, 20, 50, 100].find((value) => max / value <= count) ?? 200
  const ticks = [1]
  for (let value = step; value <= max; value += step) if (value !== 1) ticks.push(value)
  return ticks
}

function timeTicks(max: number, count = 8) {
  const step = [10, 30, 60, 120, 300, 600, 900, 1800].find((value) => max / value <= count) ?? 3600
  const ticks = []
  for (let value = 0; value <= max; value += step) ticks.push(value)
  return ticks
}

/** How often the page changes, and how many of those changes go backward. */
function countMoves(pages: (number | null)[]) {
  const known = pages.filter((page): page is number => page !== null)
  let moves = 0
  let backward = 0
  for (let index = 1; index < known.length; index++) {
    if (known[index] !== known[index - 1]) moves++
    if (known[index] < known[index - 1]) backward++
  }
  return { moves, backward }
}

function Chart({ alignment }: { alignment: Alignment }) {
  const { windows } = alignment
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)
  const [hovered, setHovered] = useState<number | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  const maxTime = Math.max(1, windows[windows.length - 1]?.end ?? 1)
  const maxPage = Math.max(1, ...alignment.pages)
  const plotWidth = Math.max(40, width - MARGIN.left - MARGIN.right)
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom
  const x = (time: number) => MARGIN.left + (time / maxTime) * plotWidth
  // Half a page of room above and below, so marks on the first and last page aren't clipped.
  const y = (page: number) =>
    MARGIN.top + plotHeight - ((page - 0.5) / Math.max(maxPage, 1)) * plotHeight

  // The DP line breaks where a window is off-topic: it has no page there.
  const paths = useMemo(() => {
    const result: string[] = []
    let current = ''
    for (const window of windows) {
      if (window.page === null) {
        if (current) result.push(current)
        current = ''
        continue
      }
      const py = y(window.page)
      current += current
        ? ` H${x(window.start)} V${py} H${x(window.end)}`
        : `M${x(window.start)} ${py} H${x(window.end)}`
    }
    if (current) result.push(current)
    return result
    // x and y only depend on these values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windows, width, maxTime, maxPage])

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    const time = ((event.clientX - box.left - MARGIN.left) / plotWidth) * maxTime
    const index = windows.findIndex((window) => time < window.end)
    setHovered(time < 0 || index < 0 ? null : index)
  }

  const active = hovered === null ? null : windows[hovered]
  const last = windows[windows.length - 1]
  const lastDecided = [...windows].reverse().find((window) => window.page !== null)
  const tooltipLeft = active ? Math.min(x(midpoint(active)) + 12, width - 280) : 0

  return (
    <div className="chart" ref={containerRef}>
      <svg
        width={width}
        height={HEIGHT}
        role="img"
        aria-label="시간에 따라 설명 중인 페이지를 raw argmax와 DP 정렬로 비교한 차트입니다. 아래 표에 같은 데이터가 있습니다."
        onPointerMove={onPointerMove}
        onPointerLeave={() => setHovered(null)}
      >
        {windows
          .filter((window) => window.page === null)
          .map((window) => (
            <rect
              key={window.window_id}
              className="off-topic-band"
              x={x(window.start)}
              y={MARGIN.top}
              width={Math.max(1, x(window.end) - x(window.start))}
              height={plotHeight}
            />
          ))}

        {pageTicks(maxPage).map((page) => (
          <g key={page}>
            <line className="grid" x1={MARGIN.left} x2={MARGIN.left + plotWidth} y1={y(page)} y2={y(page)} />
            <text className="tick" x={MARGIN.left - 8} y={y(page) + 4} textAnchor="end">
              {page}
            </text>
          </g>
        ))}
        {timeTicks(maxTime).map((time) => (
          <text key={time} className="tick" x={x(time)} y={HEIGHT - 14} textAnchor="middle">
            {formatTime(time)}
          </text>
        ))}
        <text className="tick" x={12} y={MARGIN.top + plotHeight / 2} transform={`rotate(-90 12 ${MARGIN.top + plotHeight / 2})`} textAnchor="middle">
          페이지
        </text>

        {paths.map((path, index) => (
          <path key={index} className="dp-line" d={path} />
        ))}
        {windows.map((window) => (
          <circle
            key={window.window_id}
            className={`raw-dot${hovered === window.window_id ? ' hovered' : ''}`}
            cx={x(midpoint(window))}
            cy={y(window.raw_page)}
            r={4}
          />
        ))}

        {lastDecided && (
          <text className="direct-label" x={MARGIN.left + plotWidth + 8} y={y(lastDecided.page as number) + 4}>
            DP
          </text>
        )}
        {last && (
          <text
            className="direct-label"
            x={MARGIN.left + plotWidth + 8}
            // Keep the two labels apart when both series end on the same page.
            y={y(last.raw_page) + (lastDecided && last.raw_page === lastDecided.page ? 20 : 4)}
          >
            raw argmax
          </text>
        )}

        {active && (
          <line
            className="crosshair"
            x1={x(midpoint(active))}
            x2={x(midpoint(active))}
            y1={MARGIN.top}
            y2={MARGIN.top + plotHeight}
          />
        )}
      </svg>

      {active && (
        <div className="chart-tooltip" style={{ left: tooltipLeft }}>
          <strong>
            {formatTime(active.start)}–{formatTime(active.end)}
          </strong>
          <dl>
            <dt>DP</dt>
            <dd>{active.page ?? '주제 밖'}</dd>
            <dt>raw argmax</dt>
            <dd>{active.raw_page}</dd>
            <dt>신뢰도</dt>
            <dd>{active.confidence.toFixed(2)}</dd>
          </dl>
          <p>{active.text.slice(0, 120)}</p>
        </div>
      )}
    </div>
  )
}

export function DebugScreen({ jobId }: { jobId: string }) {
  const [alignment, setAlignment] = useState<Alignment | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getAlignment(jobId)
      .then(setAlignment)
      .catch((cause) => setError(cause.message))
  }, [jobId])

  const stats = useMemo(() => {
    if (!alignment) return null
    const { windows } = alignment
    return {
      windows: windows.length,
      raw: countMoves(windows.map((window) => window.raw_page)),
      dp: countMoves(windows.map((window) => window.page)),
      changed: windows.filter((window) => window.page !== null && window.page !== window.raw_page)
        .length,
      offTopic: windows.filter((window) => window.page === null).length,
    }
  }, [alignment])

  return (
    <main className="debug">
      <header className="debug-header">
        <h1>시간별 정렬 결과</h1>
        <a className="button" href={`#/jobs/${jobId}/inspect`}>평가 라벨 편집</a>
        <a className="button" href={`#/jobs/${jobId}`}>
          슬라이드로 돌아가기
        </a>
      </header>
      <p className="muted">
        구간마다 가장 비슷한 페이지만 고르면(raw argmax) 페이지가 자주 튑니다. 순서 제약 DP는
        근거가 뚜렷할 때만 페이지를 옮깁니다.
      </p>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {alignment && stats && (
        <>
          <ul className="stat-row">
            <li>
              <strong>{stats.windows}</strong> 구간
            </li>
            <li>
              <strong>
                {stats.raw.moves} → {stats.dp.moves}
              </strong>{' '}
              페이지 변경 (raw → DP)
            </li>
            <li>
              <strong>
                {stats.raw.backward} → {stats.dp.backward}
              </strong>{' '}
              뒤로 이동 (raw → DP)
            </li>
            <li>
              <strong>{stats.changed}</strong> DP가 바꾼 구간
            </li>
            <li>
              <strong>{stats.offTopic}</strong> 주제 밖 구간
            </li>
          </ul>

          <ul className="legend">
            <li>
              <span className="swatch swatch-line" /> DP 정렬
            </li>
            <li>
              <span className="swatch swatch-dot" /> raw argmax
            </li>
            <li>
              <span className="swatch swatch-band" /> 주제 밖
            </li>
          </ul>

          {alignment.windows.length > 0 ? (
            <Chart alignment={alignment} />
          ) : (
            <p className="muted">아직 구간이 없습니다.</p>
          )}

          <details className="data-table">
            <summary>표로 보기</summary>
            <table>
              <thead>
                <tr>
                  <th>시간</th>
                  <th>DP</th>
                  <th>raw argmax</th>
                  <th>신뢰도</th>
                  <th>받아쓴 내용</th>
                </tr>
              </thead>
              <tbody>
                {alignment.windows.map((window) => (
                  <tr key={window.window_id}>
                    <td>
                      {formatTime(window.start)}–{formatTime(window.end)}
                    </td>
                    <td>{window.page ?? '주제 밖'}</td>
                    <td>{window.raw_page}</td>
                    <td>{window.confidence.toFixed(2)}</td>
                    <td>{window.text.slice(0, 80)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </main>
  )
}
