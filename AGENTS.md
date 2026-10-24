# Agent Observer — source and synchronization rules

Use `rtk` as a shell proxy when available; otherwise run commands directly.

- The canonical source has observer.py at its root and implementation under src/agent_observer. In an installation, observer.install.json identifies the canonical source; shared changes belong there.
- Project settings live in config/observer.project.json. Credentials, histories, local caches and archived data are never part of the shared bundle.
- After shared code, web UI, requirements, README, prompts or docs change, run python observer.py sync and python observer.py status from the canonical source. The running watcher performs the same synchronization. Never overwrite an installed local edit: merge it into the source first.
- Keep the root limited to README.md, AGENTS.md and observer.py. Code goes under src, configuration under config, instructions/prompts under docs, examples under examples, compatibility adapters under integrations, local data under var.
- Preserve CAD authentication, history locations and compatibility launchers. UI text is English; user prompts and document text retain their language. Never infer successful access or private reasoning from keywords.
