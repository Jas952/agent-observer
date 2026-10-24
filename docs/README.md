# Project documents in Agent Observer

## 1. A file catalog, not a required database

`docs/`, `docs/knowledge`, and Obsidian are ways to organize knowledge. None is required. The graph does not create or require Obsidian, automatically read SQL databases, or move project files.

Keep your existing `README.md`, `AGENTS.md`, `notes/`, `documentation/`, specifications folder, or Obsidian vault. Set sources in each **installation's configuration**, `config/observer.project.json`. Without explicit settings, Markdown files in `docs/` and the project root are used; a missing folder simply adds no documents.

## 2. Connect your sources

```json
{
  "schema_version": 1,
  "project": {"name": "My project", "root": "../..", "agent_label": "Project Agent"},
  "documents": [
    {"path": "README.md"},
    {"path": "documentation", "include": ["**/*.md", "**/*.txt"]},
    {"path": "notes", "include": ["**/*.md"]},
    {"path": "ObsidianVault", "include": ["**/*.md"], "group": "Reference notes"}
  ],
  "tools": [],
  "port": 8765
}
```

`project.root` is relative to the configuration file. `documents[].path` is relative to the project root. Sources must remain inside the project; symlinks pointing outside it are not indexed. Patterns apply within the specified folder. Do not include secrets or service directories.

Without Obsidian, remove the last document source. For an agent without documents, set `"documents": []`: tools, commands, working files, and routes will still work.

## 3. Hierarchy and anchors

The graph follows the project's actual folder tree. Folders expand inside their parents; files appear alongside subfolders without an artificial Root category. For example, `docs/000_Home.md` appears directly inside `docs`. Folder expansion is saved in the browser. Each folder count includes documents in its subfolders. When an accessed file is hidden, its arrow and status appear on the nearest visible parent folder. Catalog membership and recorded access are separate.

Additional anchors are **optional**. For custom grouping, use actual YAML frontmatter at the beginning of a document:

```markdown
---
graph_id: engineering-rules
graph_group: Reference / Engineering
graph_category: Specification
description: Requirements and limits for this project.
---
# Project requirements
```

`graph_id` is a unique, stable identifier; without it, the ID is derived from the path. `graph_group` specifies a logical group in event data; the document tree always follows the actual file path. `graph_category` specifies the type. You can also set a shared `group` for a source in JSON. Anchor examples inside document text are not interpreted as settings.

## 4. When a document is highlighted

The agent first reads a file. Its adapter then emits `KNOWLEDGE_ACCESS` with the relative path, or records an actual `TOOL_CALL_START/END` pair with the path in its arguments. A keyword in a prompt, a catalog entry, or a prepared manifest does not prove a read.

Passing document text to the model can be a separate event; a recorded read alone does not prove the model used it in its answer. A successful command also does not prove that the resulting model or data is correct.

New documents are discovered when the next request starts. Restart the server after changing configuration. Observer itself does not send document contents to external services.

## 5. Minimal structure for a new project

```text
project/
  README.md                 # purpose and startup
  AGENTS.md                 # agent rules, if needed
  docs/
    requirements.md         # requirements
    architecture.md         # project structure
    decisions.md            # recorded decisions
  .agent-observer/
    config/observer.project.json   # observer settings
```

This is a suggestion, not a requirement. Do not create empty articles or placeholder knowledge bases for the graph. Connect real material that helps your agent.
