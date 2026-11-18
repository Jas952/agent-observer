#!/usr/bin/env python3
"""Agent Observer: Codex lifecycle hook adapter entry point."""
from pathlib import Path
import sys

_current = Path(__file__).resolve()
_target = _current.parents[2]

if (_target / "src").is_dir():
    sys.path.insert(0, str(_target / "src"))
elif (_target / "agent_observer").is_dir():
    sys.path.insert(0, str(_target))


if __name__ == "__main__":
    try:
        from agent_observer.codex_hooks import main
        main()
    except Exception:
        # Guarantee fail-open JSON output
        sys.stdout.write("{}\n")
        sys.exit(0)
