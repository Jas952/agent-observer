# Agent Observer

## Graph and commands

The graph contains the request and refinements, an agent with its activity log, knowledge-base documents, tools and services, commands, working files, and the result. Arrows show recorded interactions; history provides step-by-step replay, and counters show token usage.

- `glob`, `grep` — find files and search text.
- `read`, `read_image` — read documents and inspect images.
- `write`, `edit`, `apply_patch` — create and modify files.
- `bash` — run shell commands. Other command types appear after a recorded call.

The graph observes the agent's actions; it does not execute commands. Connect events using the [Python example](examples/instrumented_agent.py). A file in the catalog is not evidence that it was read.

## Installation and startup

From the downloaded repository root (Python 3.10+):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r config/requirements.txt
.venv/bin/python observer.py run
```

Open http://127.0.0.1:8766/. To replay the test workflow, run `.venv/bin/python examples/replay_demo.py` in a second terminal.

For your own project:

```bash
.venv/bin/python observer.py install "/path/to/project"
.venv/bin/python observer.py run --config "/path/to/project/.agent-observer/config/observer.project.json"
```

The installed graph defaults to http://127.0.0.1:8765/. Configure documents and tools in the installation's configuration file; event delivery requires integrating the agent.

## Change rules

- Canonical source: `observer.py` and `src/agent_observer/`. An installed copy records its source in `observer.install.json`. Make shared changes in the canonical source.
- After changes, run `python observer.py sync` and `python observer.py status`. Merge installed local edits into the source first; never overwrite them forcibly.
- Keep the root limited to `README.md`, `AGENTS.md`, and `observer.py`. Code belongs in `src`, configuration in `config`, documentation in `docs`, examples in `examples`, adapters in `integrations`, and local data in `var`. Never publish secrets or histories.
- Preserve CAD authentication, history locations, and compatibility launchers. UI text is English; user text keeps its original language. Never infer private reasoning or unobserved actions.
- Use `rtk` for terminal commands when available.
