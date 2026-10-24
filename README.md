# Agent Observer

A local, interactive graph of an AI agent's workflow, from the request and requirements to tool calls and the final result. It shows interactions recorded by agent events.

- Highlights tool calls, successful actions, and errors.
- Documents, commands, and working files in one graph.
- Request history and step-by-step route replay.
- Token tracking with separate estimates and provider-reported usage.

![Graph after a complete test cycle; all data is fictional](docs/assets/graph-demo.png)

The document category is based on a project **knowledge base**: Markdown notes with YAML descriptions and Obsidian links. Projects share a structure but keep their own content; the knowledge base grows as the agent works. Example structure (other folders are supported too):

```text
docs/knowledge/
├── ContentStrategy.md
├── Audience.md
├── Decisions.md
├── ideas/
│   └── ContentIdeas.md
├── publication/
│   └── PublicationQueue.md
└── analytics/
    └── Performance.md
```

See [AGENTS.md](AGENTS.md) for installation, startup, and configuration.
