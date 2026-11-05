"""A generated test PDF and a matching mixed Korean/English mock transcript.

These let the whole pipeline run before real sample data exists.
"""

from pathlib import Path

import pymupdf

from app.models import Segment

FOOTER = "CS101 Algorithms - Lecture 5"

# Each slide is a title and its bullet points.
SLIDES: list[tuple[str, list[str]]] = [
    (
        "Recursion",
        [
            "A function that calls itself on a smaller input",
            "Base case: the condition that stops the recursion",
            "Recursive case: reduces the problem toward the base case",
        ],
    ),
    (
        "Factorial and the Call Stack",
        [
            "factorial(n) = n * factorial(n - 1), factorial(0) = 1",
            "Each call pushes a new stack frame",
            "Too many frames cause a stack overflow",
        ],
    ),
    (
        "Time Complexity",
        [
            "Big-O describes growth as the input size increases",
            "Recurrence relation: T(n) = T(n - 1) + O(1)",
            "Linear recursion runs in O(n) time",
        ],
    ),
    (
        "Merge Sort",
        [
            "Divide the array into two halves",
            "Conquer: sort each half recursively",
            "Merge the two sorted halves in O(n) time",
            "Total running time is O(n log n)",
        ],
    ),
    (
        "Memoization",
        [
            "Store the results of subproblems in a table",
            "Naive Fibonacci recursion takes exponential time",
            "Memoized Fibonacci takes O(n) time",
        ],
    ),
]

# This slide also gets a vector chart and an embedded image, from _draw_figures.
FIGURE_SLIDE_TITLE = "Comparing Algorithms"
SLIDES.append((FIGURE_SLIDE_TITLE, ["Measure on real inputs"]))

# (start, end, text). Covers forward moves, going back to slide 1, and an off-topic span.
_TRANSCRIPT: list[tuple[float, float, str]] = [
    (0, 14, "오늘은 recursion을 배울 거예요. recursion은 function이 자기 자신을 다시 호출하는 거죠."),
    (14, 28, "여기서 제일 중요한 건 base case입니다. base case가 없으면 recursion이 멈추지 않아요."),
    (28, 44, "이 recursion이 base case에서 멈추고, recursive case는 문제를 더 작은 input으로 줄여 갑니다."),
    (44, 60, "base case 빠뜨리는 실수는 시험에 꼭 나옵니다. 이건 꼭 외워 두세요."),
    (60, 76, "Let's look at factorial. factorial of n is n times factorial of n minus 1, and factorial of 0 is 1."),
    (76, 92, "Every time we call factorial, a new stack frame is pushed onto the call stack."),
    (92, 108, "Think of the call stack as a pile of plates. You can only remove the plate on top."),
    (108, 124, "If n is around one hundred thousand, you get a stack overflow, so remember that for the exam."),
    (124, 140, "이제 time complexity를 볼게요. Big-O는 input size가 커질 때 실행 시간이 어떻게 증가하는지 나타냅니다."),
    (140, 156, "recurrence relation T(n) = T(n - 1) + O(1)을 풀어 보면 linear recursion은 O(n)이 됩니다."),
    (156, 172, "교수들이 recurrence relation을 정말 좋아해요. T(n)을 직접 전개하는 연습을 해 보세요."),
    (172, 186, "잠깐, 다시 base case 얘기로 돌아가 볼게요. recursion에서 base case가 stop condition이라고 했죠."),
    (186, 200, "recursive case가 base case 쪽으로 input을 줄이지 않으면 function이 영원히 자기 자신을 호출합니다."),
    (200, 214, "자, 잠깐 공지할게요. 다음 주 수요일에는 휴강이고, 점심 먹고 2시에 조교님이 출석을 부를 거예요."),
    (214, 228, "주차장 자리가 없으니까 대중교통 타고 오세요. 날씨도 추워지니까 감기 조심하시고요."),
    (228, 244, "Okay, merge sort. We divide the array into two halves and sort each half recursively."),
    (244, 260, "The merge step walks both sorted halves with two pointers, which takes O(n) time."),
    (260, 276, "Merge sort needs extra O(n) memory for merging, and that is not written on the slide."),
    (276, 292, "There are log n levels and each level costs O(n), so the total running time is O(n log n). This is important."),
    (292, 308, "마지막으로 memoization입니다. subproblem의 결과를 table에 저장해 두고 다시 계산하지 않는 거예요."),
    (308, 324, "naive Fibonacci recursion은 같은 subproblem을 반복해서 exponential time이 걸립니다."),
    (324, 340, "fib(50)을 memoization 없이 돌리면 몇 시간이 걸려요. 직접 돌려 보면 차이가 확 느껴집니다."),
    (340, 372, "memoized Fibonacci는 O(n)이고, 이 비교는 기말고사에 나오니까 꼭 정리해 두세요."),
    (372, 388, "자 이 bar chart 보면 merge sort의 running time이 naive recursion보다 훨씬 작습니다."),
    (388, 404, "Comparing algorithms this way, measure them on real inputs instead of guessing."),
]

# Ground truth for the transcript above, in the format eval_alignment.py reads.
GROUND_TRUTH: list[dict] = [
    {"start": 0, "end": 60, "page": 1},
    {"start": 60, "end": 124, "page": 2},
    {"start": 124, "end": 172, "page": 3},
    {"start": 172, "end": 200, "page": 1},
    {"start": 200, "end": 228, "page": None},
    {"start": 228, "end": 292, "page": 4},
    {"start": 292, "end": 372, "page": 5},
    {"start": 372, "end": 404, "page": 6},
]


def mock_transcript() -> list[Segment]:
    return [Segment(start=start, end=end, text=text) for start, end, text in _TRANSCRIPT]


def _draw_figures(page: pymupdf.Page) -> None:
    """Draw a bar chart with vectors on the right, and embed an image below the text."""
    shape = page.new_shape()
    base = 460
    shape.draw_line((540, 180), (540, base))  # Axes.
    shape.draw_line((540, base), (900, base))
    shape.finish(color=(0, 0, 0), width=1.5, closePath=False)
    for index, height in enumerate((80, 170, 250)):
        left = 570 + index * 110
        shape.draw_rect(pymupdf.Rect(left, base - height, left + 70, base))
    shape.finish(color=(0.1, 0.2, 0.5), fill=(0.5, 0.65, 0.9), width=1.5)
    shape.commit()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pixmap.set_rect(pixmap.irect, (40, 140, 120))
    page.insert_image(pymupdf.Rect(80, 270, 280, 470), pixmap=pixmap)


def make_test_pdf(path: Path, slides: list[tuple[str, list[str]]] = SLIDES) -> None:
    """Write 16:9 slides with a repeated footer and page numbers."""
    document = pymupdf.open()
    font = pymupdf.Font("cjk")  # Bundled with PyMuPDF; covers Hangul and Latin text.
    for number, (title, bullets) in enumerate(slides, start=1):
        page = document.new_page(width=960, height=540)
        page.insert_font(fontname="cjk", fontbuffer=font.buffer)
        page.insert_text((60, 90), title, fontname="cjk", fontsize=36)
        y = 170
        for bullet in bullets:
            page.insert_text((80, y), f"- {bullet}", fontname="cjk", fontsize=22)
            y += 60
        if title == FIGURE_SLIDE_TITLE:
            _draw_figures(page)
        page.insert_text((60, 515), FOOTER, fontname="cjk", fontsize=12)
        page.insert_text((890, 515), str(number), fontname="cjk", fontsize=12)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()
