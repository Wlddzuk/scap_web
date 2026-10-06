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

# Modules call load_dotenv(), which never overrides a variable that is already
# set. Blanking the paid image key here keeps a test that forgets to stub a
# generator from spending real FAL credit. Export FAL_KEY in the shell to run
# an intentional integration test.
os.environ.setdefault("FAL_KEY", "")
