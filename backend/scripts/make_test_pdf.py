"""Write the generated test PDF, to use until real slides are in samples/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.samples import make_test_pdf  # noqa: E402

if __name__ == "__main__":
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "test_slides.pdf")
    make_test_pdf(output)
    print(f"Wrote {output}")
