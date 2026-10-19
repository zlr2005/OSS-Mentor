"""Observable and interrupt-safe stage 3-5 training entry point.

Progress is printed to the terminal and persisted under the selected run directory.
Press Ctrl+C once to record a clean ``_CANCELLED`` terminal state.
"""

from coder_issue.training.pipeline import main


if __name__ == "__main__":
    raise SystemExit(main())
