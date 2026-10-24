#!/usr/bin/env python3
"""Agent Observer command entry point. Implementation: src/agent_observer."""
from pathlib import Path
import sys
_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root / "src"))
sys.pycache_prefix = str(_root / "var/cache/python")
from agent_observer.cli import main
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
