"""Make the repository's top-level modules importable in every pytest mode."""

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Provider-failure tests must not append fake entries to the real
# instance/summarizer_errors.log that operators read.
os.environ.setdefault(
    "SUMMARIZER_ERROR_LOG",
    os.path.join(tempfile.mkdtemp(prefix="clipper-tests-"), "summarizer_errors.log"),
)
