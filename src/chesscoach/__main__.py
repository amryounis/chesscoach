"""Allow `python -m chesscoach`."""

import sys

from .cli import main

sys.exit(main())
