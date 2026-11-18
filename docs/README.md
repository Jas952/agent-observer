# Project resources in Agent Observer

## Interface language

The bottom-left **EN / RU** button beside Theme, History and localhost switches the dashboard between English and Russian without reloading. The choice is stored in this browser and carried to another project's graph. `?lang=en` or `?lang=ru` overrides the saved preference; English remains the initial default.

Navigation, settings, dialogs, tooltips, accessibility labels, and Observer diagnostic messages are localized. Project/session names, command and MCP identifiers, model names, paths, document contents, captured requests, results, and terminal output remain unchanged. Language affects presentation only, not configuration, telemetry, or history.

Browser translation opt-outs on `html` or `body` do not disable Observer's own language switch. Nested `translate="no"` content remains protected, including technical names and captured text.

Controls use flat icons with a neutral hover or selected background in both themes. Folder headers group the session count, terminal and project settings on one baseline; unavailable terminals remain dimmed. The header and browser title temporarily use the fixed name **Agent Observed**, without a logo; switching folders never replaces this application identity. Functional folder names, session names and terminal context remain visible.

Select fields reserve space for an inset chevron. On narrow screens, project settings place the filter above a single aligned search/actions row. Category headers use subtle neutral separators and plain counts; visible-resource eyes have no permanent filled tile. Keyboard focus marks the disclosure chevron rather than outlining the entire row, and Enter/Space still expand or collapse the category. Dialog containers do not receive an outer focus ring; project settings initially focus their heading.

Theme-wide button defaults have lower priority than component appearance. Document bubbles retain their circular borders, fills, selection rings and recorded activity colors in both themes instead of inheriting flat toolbar-button styling.

## 1. A file catalog, not a required database

`docs/`, `docs/knowledge`, and Obsidian are ways to organize knowledge. None is required. The graph observes agent commands rather than re-executing them. Explicit service controls can start or stop trusted local-service definitions; they do not execute commands supplied by browser requests or telemetry. Moving a document requires an explicit user action. RTK's own telemetry database is read-only and used only to match its reported counters to recorded shell calls.

Keep your existing `README.md`, `AGENTS.md`, `notes/`, `documentation/`, specifications folder, or Obsidian vault. Set sources in each **installation's configuration**, `config/observer.project.json`. Without explicit settings, Markdown files in `docs/` and the project root are used; a missing folder simply adds no documents.

## 2. Connect your sources

```json
{
  "schema_version": 1,
  "project": {"name": "My project", "root": "../..", "agent_label": "Project Agent"},
  "documents": [
    {"path": "README.md"},
    {"path": "documentation", "include": ["**/*.md"]},
    {"path": "notes", "include": ["**/*.md"]},
    {"path": "ObsidianVault", "include": ["**/*.md"], "group": "Reference notes"}
  ],
  "capabilities": {"mcp": [], "skills": []},
  "vault_path": "",
  "commands": {"hidden": []},
  "block_layouts": {"commands": "list", "tools": "list", "capabilities": "list", "files": "list"},
  "tools": [],
  "port": 8766
}
```

`project.root` is relative to the configuration file. `documents[].path` is relative to the project root. These sources must remain inside the project; symlinks pointing outside it are not indexed. Patterns apply within the specified folder. An optional `vault_path` explicitly authorizes a separate Obsidian folder, including an external vault; its files use `@vault/` catalog paths and are restricted to that vault root. Do not include secrets or service directories.

Without Obsidian, remove the last document source. For an agent without documents, set `"documents": []`: tools, commands, working files, and routes will still work.

## 3. Hierarchy and anchors

The graph follows the project's actual folder tree. Both document views use the same complete catalog. External connections reach a fixed Documents input boundary; captured read results have a separate return boundary and connect back to the read command and agent. No return is invented before a recorded completion, and this does not claim that the model used the document's contents. The linked-document counter opens the registered endpoints with call numbers. Collapsed folders reflect descendant activity; hidden or scrolled-out file endpoints resolve to their nearest visible ancestor or a stable tree boundary anchor. Catalog membership and recorded access remain separate.

Documents observed in the selected route remain in both views and in call attribution if their catalog source is later removed. They do not leak into unrelated routes. Retained history does not register paths or extend current file-access permissions.

### List and bubble views

Use **List / Bubbles** in the Documents navigation row to switch between the collapsible file tree and relationship map. Switching does not remove catalog entries.

- List is one filesystem tree, not a sidebar of category roots. `docs/` contains its actual files and subfolders; root documents and external-vault paths remain reachable alongside it. Full filenames retain `.md` and other extensions and use regular weight regardless of activity. Rows have no card perimeter or full-row activity fill: a leading Finder-style colored marker conveys recorded activity, including inherited activity on ancestor folders. Idle markers remain invisible. Keyboard focus is indicated on the filename or disclosure control, not around the whole row. Search reveals matching nested files and their ancestors. Expansion is saved per project; the block stays fixed while its tree scrolls vertically.
- Bubbles represent registered documents and documents retained by the selected recorded route. Muted links come from actual Markdown links and Obsidian-style `[[document]]` or `[[document|alias]]` references, including relative paths and heading fragments. External URLs, code examples, unresolved references and ambiguous short names do not create links. Reciprocal references share one visual link. A link is not evidence that the agent read either document.
- Hover or focus a bubble for its full path and description. Captions choose free space below, above or beside their bubble, avoiding all other bubbles, highlight rings and captions throughout animation and dragging. Active, selected, hovered and focused bubbles do not bypass this check. If no placement fits, only the caption hides; the full path remains in the tooltip and selected-document bar. Click or press Enter to open the existing document viewer; the selected-document actions provide Markdown editing and comments. Drag a bubble to pin it, or use arrow keys (Shift for larger moves). Escape dismisses the tooltip. Positions are saved per project in this browser.
- List and Bubbles share the same block width, height and position. Switching views never creates a separate square size or restores a different per-view size. Resize handles update one shared browser-local size. Earlier separate view preferences migrate once using the saved List dimensions; later resizes are not overwritten on reload. Reset layout clears the shared custom size.
- Automatic placement forms organic clusters around connected hubs, with unlinked documents farther out. There is no grid or invisible movement boundary. Only manually pinned positions persist; earlier normalized pins are migrated. The initial camera fits the settled layout, without undoing subsequent manual zoom, pan or dragging. Access arrows stop at the Documents boundary instead of drawing tracks through the list or bubble map. After an incoming arrow arrives, its document receives a circular marker in the recorded call's color. Muted document-reference links remain visible. Captured completions return along the same external access path, not a second parallel route.

The map has independent **25–400% zoom**. Use wheel/trackpad pinch, the local `−`/`+` buttons, or keyboard `+`/`−`; click the percentage or press `0` to fit document positions again. Drag empty map space to pan, or drag a bubble to pin it, including beyond the initial viewport. Camera changes do not rewrite document positions or reroute external arrows. Low-priority labels hide when zoomed far out; hovering or focusing a bubble provides its full path in a normal-size tooltip. Circular selection stays separate from activity coloring. The local camera survives List/Bubbles switching within the page; reload starts with the library fitted.

Pointer grabs do not recenter the document camera; keyboard focus can still reveal an offscreen document. A local drag or pan owns its coordinate frame: zoom/reset controls and keyboard nudges are ignored until it ends. Capture loss, window blur, hiding the map and removing the dragged document clear ownership; an actual host resize ends the gesture before adjusting the camera. Local camera and force-layout updates do not request external-route redraws. List keeps one visible keyboard entry after filtering, collapsing or returning from Bubbles, and catalog rebuilds preserve a focused row by document/folder identity.

A catalog/link refresh leaves a held, unpinned bubble at its grabbed coordinates instead of moving it to a new automatic-layout target.

New documents and links are refreshed when the dashboard opens or the next request starts. Reload the dashboard after changing document references on disk.

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

New documents are discovered when the next request starts or the dashboard catalog refreshes. Defaults saved through block settings apply immediately without restarting the server. After editing configuration JSON externally, use **Reload settings** to read its current values; server-code updates still require a restart. Observer itself does not send document contents to external services.

Refreshing settings, rediscovering tools or changing connection modules preserves the running process's listener port, authentication token path, history path and storage directory. Restart Observer to change these launch-level settings.

## 5. Resource decisions and unobserved resources

An adapter can emit `RESOURCE_DECISION` when it has an explicit assessment that a resource was not required, or that a required action was bypassed. `Not observed` means no access was captured; without a decision event, intent and necessity are unknown. Observer cannot distinguish “forgot” from “not needed” by looking at an unused resource or a clearance value in the prompt. Existing CAD adapters do not generate these assessments automatically.

### Event payload

```json
{
  "type": "RESOURCE_DECISION",
  "run_id": "run_123456",
  "target_id": "docs/knowledge/cad_domain/Инженерные стандарты.md",
  "decision": "not_required",
  "reason": "Standard metric thread M6 specified; standard tolerance table lookup is not required.",
  "requirement": "Section 4.1 Thread Standards"
}
```

- `type`: `"RESOURCE_DECISION"` (required).
- `run_id`: Active run identifier (required). Missing or mismatched run IDs are rejected. Like other detached telemetry, events received with no active run are ignored.
- `target_id`: Target resource identifier (required, non-empty string). Matches an existing document, tool, or command node ID, an exact node label, or a registered document path (relative POSIX path, project-relative path, or unique filename). Missing or ambiguous targets fail validation immediately without state mutation.
- `decision`: Must be either `"not_required"` or `"bypassed"` (required, case-insensitive). Other decision values are rejected.
- `reason`: Non-empty justification string describing why the resource was deemed not required or bypassed (required).
- `requirement`: Optional reference string (e.g. specification clause, standard identifier, or user constraint).

### Status semantics and state isolation

- **Usage status preserved**: Emitting a `RESOURCE_DECISION` preserves the resource's operational status independently. It does not mark the resource as hit (`status: "hit"`), does not mark it active (`status: "active"`), does not create a graph interaction edge (no `agent-hub` -> target edge), and does not alter `latest_resource_id`. Idle resources remain `status: "idle"`.
- **Node decision snapshot**: The node records `node.resource_decision = {decision, reason, requirement, step}`, where `step` is the 1-based event sequence number in the route.
- **Persisted history snapshots**: Idle nodes with recorded resource decisions are retained in step snapshots and saved route history, ensuring explicit decisions survive history scrubber replay.
- **Subsequent actual access**: If an evaluated resource is subsequently accessed via `KNOWLEDGE_ACCESS` or `TOOL_CALL_START/END`, its decision record is retained while its status updates to reflect actual access (`hit` or `error`). The UI prioritizes evidence of actual access while preserving the decision history; the field provides the latest recorded assessment, not current usage.
- **Rejection without mutation**: Invalid decisions, empty reasons, and missing or ambiguous targets fail with validation errors (HTTP 422) before changing orchestrator phase or graph state.
- **No inferred expectations**: Observer does not infer triggers from prompt text, guess required documents, or claim resources were forgotten automatically. Resources without explicit events remain unassessed; legacy and older history routes without decisions remain unassessed.

## 6. Minimal structure for a new project

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


## Codex installation and capture

The fixed-size connection icon in the header opens **Agent connections**. The active project's name appears beside the logo, with its path available on hover; History sits beside the local server address at the bottom. The dialog contains saved/pinned project folders and a staged setup flow: folder → role → analysis → graph generation. Model and authentication settings remain with the agent.

**Folder role** is a draft selection, not a connection action. **Agent system** shows only **Project agent**; **Working repository** shows only **Codex Desktop / CLI**. Changing this selection never activates a runtime. A folder's Project settings role controls open the same staged setup for that folder instead of bypassing it.

**Analyze folder** reads role-relevant project components and session evidence. The report has component checks, diagnostic logs and sessions matched to the selected folder, not the graph currently open behind the dialog. Missing optional resources and event-only sections are distinguished from blocking prerequisites. **Generate graph** remains disabled until a matching ready report exists. Editing the path, selecting another folder, changing the role, a failed scan or a rejected stale generation clears that readiness.

Analysis walks the folder without a depth cutoff, with a 20,000-file safety ceiling. Dependency/build directories, Observer-local data, Obsidian settings and directory symlinks are excluded; a partial/inaccessible scan blocks generation instead of claiming complete coverage. Markdown sources, MCP/skills/native declarations, local tool metadata and configured memory references are inventory, not proof of calls or service health. Prompt, file-operation and result sections wait for recorded events. All folder-matched Codex index entries are listed, including unreadable/compressed journals and unknown activity; there is no “latest 20” session cutoff.

Generation rechecks the source fingerprint and Codex index readability. Newly discovered Markdown sources are merged into the selected graph's document catalog only at this explicit step, preserving existing configuration. Embedded-session previews read the selected folder's own history and exclude external Codex runs; library/example references alone do not prove an embedded agent integration.

**Project agent** observes the agent implemented inside the selected project, not an external development agent working on its code. For CAD Studio, this is the Swift application's Direct API or Harness path through its configured router. Direct API does not execute native tools; Harness supplies its own tools. OMP uses its extension. A custom agent must emit its real lifecycle using `examples/instrumented_agent.py`; an installed graph alone is not an active agent connection. **Ready**, **Connected**, and **Active** distinguish available integration, attached observation, and recorded activity.

If embedded-agent integration is missing, the failed analysis exposes **Integration instructions** with a copyable project-scoped prompt. Copying it does not invoke a model or activate observation.

**Codex Desktop / CLI** passively reads project-matching metadata from Codex's read-only SQLite index and tails its own session JSONL files. It observes the existing desktop application and ordinary CLI, including noninteractive `exec`; it does not resume threads, launch a substitute app-server, edit model/provider/OAuth settings, approve tools or change trust. The desktop on this Mac is installed as `ChatGPT.app`, bundle `com.openai.codex`; `source=vscode` with `originator=codex_work_desktop` identifies its desktop sessions. A real bundled CLI run reported `source=exec`, `originator=codex_exec`.

| Command | Purpose |
| --- | --- |
| `observer.py connect "/project" --module project` | Configure and run the selected project's existing agent integration. |
| `observer.py connect "/project" --module codex` | Configure and run passive observation of its existing Codex Desktop/CLI. |
| `observer.py connect "/project" --module codex --no-run` | Configure only; an already running compatible graph can activate the module immediately. |
| `observer.py run --config "/project/.agent-observer/config/observer.project.json"` | Start the graph with its saved module. |

Choosing a folder through the sidebar, path field or macOS picker previews settings in place, without leaving or modifying the current graph. There is no separate project-title/action row or **Switch project** shortcut. Only **Generate graph**, after successful analysis, installs/configures observation as needed and opens the selected folder's verified local graph. For the current folder it applies the graph in place; another folder opens only after explicit generation. Docs List/Bubbles and browser preferences remain project-scoped. Changing the activated module replaces its native catalog and live routes without moving the canvas; saved sessions remain in History and late events from the other module are ignored. A port belonging to another project/service is not accepted. Selecting passive capture removes only obsolete Observer-owned Codex hooks, preserving other callbacks.

`POST /api/connections/analyze` accepts the folder, draft repository role and current workspace revision. It does not install, connect or write project settings. `POST /api/connections/generate` additionally requires the matching ready analysis ID and revision; missing or stale proof is rejected before activation. The existing local authentication and Host/Origin guard protect both actions. Direct CLI connection commands remain explicit operator actions outside this dialog workflow.

### Recorded activity and limits

Attachment normally baselines existing rollouts at committed EOF. A recent, explicitly unclosed turn is recovered from its recorded start so connecting during work does not discard its prompt or earlier tool calls. Completed older conversations are not replayed as live; newly created or updated sessions after attachment remain observable. Only complete JSONL records are committed; partial UTF-8 lines, truncation and inode replacement cannot become duplicate live calls. **Load latest recorded turn** is explicit historical import, not a claim that the desktop is currently executing. Compressed archives are reported as unsupported rather than silently accepted.

Before the first new call, Commands reads tool declarations from the latest matching project's uncompressed Codex session. This includes structured nested declarations exposed through `exec`, not arbitrary tool-name mentions. Conversation history is not replayed, and the catalog creates no usage counters or routes. Another project's declarations are never borrowed; without a matching schema, native capabilities remain unknown. Exact Codex command names keep separate cards and counters, and incremental namespace updates retain previously declared commands.

CAD Commands also reads enabled native tool declarations from the locally installed Harness packages and the base/headless profiles selected by the Swift application. Discovery reads public metadata and source declarations without executing tools or JavaScript. It does not borrow Codex/OMP commands or mark declarations as used: counters and routes require recorded calls. Other custom runtimes populate their native catalog through reported inventory and observed operations.

The desktop's deployed persisted stream has completed operation records with source timing, not a proven public subscription to live tool-start notifications. Observer never invents an in-progress start for these completions. Public item records cover commands, MCP operations, file changes, hosted search, final messages and usage. Provided tool namespaces populate runtime inventory; configured/discovered resources alone do not prove connection or use. Unsupported source records remain distinguishable in the sanitized journal.

Source records live beside private graph history in `agent-events.sqlite3`; `GET /api/agent-events?application=Codex&after=0&limit=200` provides cursor paging independently of compact route snapshots. Credentials, system/developer message bodies and private reasoning are omitted; payload size limits remain explicit. There is no separate top-level Codex events tab.

Working Files/Docs use explicit tool arguments, structured parsed-read paths and actual file-change paths. Arbitrary script-internal filesystem I/O is not an OS audit. Result shows the published response and observed mutations; unavailable pre-change baselines remain unknown. Usage uses source-reported response/turn identities rather than adding cumulative thread totals again.

When Codex reports a command's working directory and parsed command text, literal file operands of simple `cat`, `head`, `tail` and `sed` reads also establish file evidence, including RTK wrappers and `file://` working directories. Shell expansions, wildcards, compound scripts and paths mentioned inside arbitrary code are not inferred as file reads.

CAD's project module discovers configured project MCP services, separately installed agent skills and CLI integrations; it does not import local or global OMP tool catalogs. Project reference files in `docs/skills` remain in Documents rather than becoming skill plugins. The Codex module uses Codex's own inventory and skill locations. New Codex/custom installations do not inherit CAD memory, credentials or history.

### Optional interactive full-stream relay

The existing `observer.py codex \"/project\"` command remains an explicit advanced interactive-CLI relay. It forwards the native authenticated App Server protocol, including approvals and start notifications, using the existing model/provider. It cannot attach to an already running desktop conversation. Do not run passive observation and the relay for the same session simultaneously. The legacy hook/install commands remain available for explicit instrumentation, but hooks are not required for the two-module passive connection.

## Sessions, routes and token usage

History groups request routes by the explicit `session_id` and application/project identity. Without a session ID, older routes remain grouped by project and are labelled as legacy project history. Existing histories are not rewritten to invent missing model or application information.

Live and History retain the selected session's own runtime inventory; a previous Codex route does not declare the tools available to a CAD project agent. Explicit `?session=` links bootstrap the requested session's graph, catalog and full captured history before first paint.

The left **Sessions** sidebar groups chats under their saved project folders, with collapsible project headings, indented chat rows and a subtle current-chat highlight. Selecting a chat switches its request, calls, result, token counters and timeline together without navigating or reloading the page, including across project folders. Unchanged folder/chat DOM nodes and sidebar scroll are retained, and live disclosure state is read before updating changed folders. Returning restores the chat's inspected timeline position and camera; a chat following live continues at its latest captured event. Up/Down, Home and End move focus; Enter/Space open a chat or expand a project. Folder expansion belongs to the dashboard sidebar and is not replaced by the destination project's preferences. A discovered chat without readable request evidence opens an idle graph, never another chat's activity.

Recent chat details are cached and adjacent chats are prefetched with limited concurrency. Repeated selections share an in-flight request, and cached selections need no additional session-detail request. A first uncached selection keeps the previous graph visible but noninteractive while the requested state loads; a subtle row spinner identifies that selection. Late responses cannot replace a newer selection. Catalog rendering is skipped when configuration is unchanged, and inactive detail caches are bounded.

Project caches are separate even when two folders have identical session IDs. Up to three ordinary browser project contexts are retained; unsaved document/link/memory drafts are protected from eviction. Switching projects changes the browser-local storage namespace and restores visibility, pins and document view. Stale requests and WebSocket messages cannot update the selected project. First visits initialize an isolated project runtime and load its history inside the existing host; they never allocate another localhost port.

Live graph updates coalesce at the next `requestAnimationFrame` while every captured event remains in history. Gesture deferral preserves the inspected prefix until interaction ends. Panning changes the world transform without rewriting scale-dependent CSS variables and arrow sizes on every pointer event. Localization processes only changed attributes/text and deduplicated added subtrees, without observing its own translation writes. There is no fixed 30/60 FPS cap: browser scheduling, display refresh rate and available rendering resources determine frame cadence.

For indexed Codex chats without an Observer capture, the session detail endpoint can project the latest recorded turn from that chat's existing, safe, uncompressed rollout through the normal graph reducer. This read-only projection preserves full result text, timestamps, calls and usage without resuming an agent, creating artifacts, rewriting history or counting usage again. Unavailable evidence remains an idle graph, not another session's activity.

`GET /api/sessions` lists session metadata and `GET /api/sessions/{session_id}` retrieves that session's graph, catalog and history. The existing local authentication and origin restrictions apply. Session discovery is not evidence of current execution; unknown activity remains labelled unknown.

Cross-project state uses authenticated `POST /api/project-request` and `GET /api/project-file` transport to registered, isolated ASGI runtimes inside one Observer process at **http://127.0.0.1:8766/**. `GET /api/bootstrap?session=...` supplies selected-session data without credentials. The browser retains one origin and token; per-project credentials, settings, histories and reducers remain isolated server-side. `/ws?project=...` connects to the selected runtime without another listener. Project-list, naming, deletion and scoped-settings operations retain explicit folder arguments.

A running chat has a green pulsing indicator, including while a different chat is selected. Completion stops the pulse; waiting and unknown activity do not animate. The folder icon is green when it contains a running chat. Reduced-motion preferences retain a static indicator instead of animation.

Each chat has a trailing **⋯ → Rename session** action. Names accept 1–120 single-line characters; Save persists an Observer display name, while Cancel/Escape leaves the name unchanged. `POST /api/session-name` accepts the registered project, saved repository role, session ID and name; unknown sessions and changed roles are rejected. Names are stored in private `session-names.sqlite3` beside that project's Observer history, keyed by project path, role and session ID, so restarts and other graph windows retain them without editing Codex journals, recorded names or agent settings.

**⋯ → Delete session** opens a confirmation with Cancel initially focused. Deletion removes the chat from Observer only: original agent chats, source journals and project files are preserved. `POST /api/session-delete` requires explicit confirmation and a registered folder/role/session match. Durable tombstones share the private session-name database and prevent discovery, late events, cached details and restarts from resurrecting the deleted Observer session. Deleting a foreign folder's chat cannot evict a same-ID session in the current folder or another repository role.

`GET /api/session-projects` supplies current and saved folders' session metadata without starting or resuming an agent. Embedded history and external Codex/OMP journals remain role-scoped. Loaded runtimes contribute current metadata without hiding native discoveries; stale or unknown evidence is never promoted to running. Clicking a foreign chat validates its registered folder, role and session before initializing its isolated runtime on the existing host.

Normal `observer.py run` and connection commands use **127.0.0.1:8766**. Installation no longer searches for a separate free port per project. Startup verifies same-user Python processes against canonical/registered Observer script paths and start identities: it reuses an identical host, gracefully replaces an outdated verified host and stops extra verified Observer servers. An unrelated or unverifiable listener is never killed or bypassed with another port; startup reports the conflict. After source updates, run the normal command again to replace the verified host. Explicit `run --port` remains available for isolated administrative/test instances. Agent applications, model/router settings and histories are not replaced.

The top-left sidebar button shows/hides projects and chats; the right inspector toggle stays in the top-right corner. **Route details** occupies the bottom slot of the slim left icon rail, aligned with its selection/pan and zoom/layout controls. History, Theme, Language and localhost remain in the footer. Toolbar pointer presses do not select text; keyboard focus remains accessible. Graph and Agent connections stay in the header, and folder headings open project Terminal and settings.

The header's two small, unboxed status icons are indicators, not action buttons: **Observer connection** reports WebSocket connectivity to the selected project's Observer, while **Session status** reports its captured agent phase. Connection is not a claim that an external tool/model is online, and Completed is not proof of result correctness. Icons distinguish offline/error, running, waiting, idle and completed states by shape as well as color. Hover or keyboard focus shows localized labels through the shared tooltip; Escape dismisses it. Running animation respects reduced-motion preferences. The project/chat hierarchy retains flat folder/chat controls, monospace labels and a subtle selection cue. Folder gears do not expand or select a chat. Compact windows keep the two-row header and drawer below it; the short-window rail scrolls to keep its controls reachable.

Layout choices and zoom presets open immediately beside their rail button and remain inside the viewport. The actual-size button and shortcut-number suffixes in control tooltips are removed; the functional zoom percentage remains. Tooltips cancel on activation, scrolling, focus loss and modal changes, and cannot reappear without a new qualifying hover or keyboard focus.

Passive Codex activity is journal evidence, not a process heartbeat: an unclosed turn with a journal modified within 30 minutes is labelled active; stale unmatched turns are unknown. Abnormal process termination can therefore remain indistinguishable from a long model/tool wait until that evidence expires. Project-agent sessions require explicit session IDs in real lifecycle events. Switching repository roles filters session discovery to the selected module rather than combining embedded and external agents.

Send these optional string fields with `PROMPT_START`: `application`, `session_id`, `session_name`, `project_id`, `requested_model`, `backend`. Use the same session ID for related requests. Send `MODEL_INFO` with `model` or `router_model` only when the transport actually reports that identifier. A requested router alias such as `routing` does not identify the resolved provider model. Session metadata is stored with each route, alongside the configured knowledge source paths and a bounded snapshot of the project's top-level structure. Database contents are not scanned.

Call order shows the observed category path. Selecting a call pins its recorded route on the graph without opening a modal or changing the current inspector visibility; exact arguments and results remain available through resource details. Live updates do not replace a manually inspected route. Prompt text, specification, refinement comments and attachments remain attached to their request.

The route viewer and call list organize repeated interactions and chronology:
- **Focused connections**: One presentation stage is emphasized, with small, unboxed text such as `#12 — +2.30 s`. Completed category-to-category paths remain as readable, muted traces; repeated calls reuse those paths rather than adding a line per invocation. Visible strokes remain thin across the entire 15–180% zoom range: 1.7 CSS pixels for active routes and 1 pixel for history. The faint underlay follows the current routed geometry rather than leaving a second stale branch. Exactly one arrowhead indicates the current input or captured-return direction; changing direction removes the previous arrowhead. Travel takes 1.4–3 seconds depending on path length, with eased acceleration and deceleration rather than a short per-hop flash. Delivery is ordered: the external arrow docks, the frame marker responds, the matching card marker responds, then the card outline appears. A settling beat completes before the next hop starts. Captured results return along the same route. Pending input does not expire on a timer. Request paths stay dashed from the first frame. Manual selections remain until navigation; usage counters, history and token totals retain their actual recorded values independently of visual playback.
- **Connection controls**: The connections icon on the left canvas toolbar opens **Connections and call details**, including **Selected call / Overview**. Overview retains faint access traces without highlighting a call. Selected call restores and pins the current invocation; Call order and resource activity can select any recorded invocation. In parallel work, a completion belongs to its matching call ID rather than the latest-started call. Animation queues recorded starts and completions in event order without delaying execution or inventing dependencies between concurrent operations. No information panel opens on hover.
- **Routing while moving blocks**: External paths terminate outside category boundaries, never inside cards. Frame and card markers share the receiving card's vertical center, including later grid columns and visible scroll pins. Document Bubbles omit these connector dots at both the frame and the document; the bubble's own activity ring provides arrival feedback, while List keeps its connector markers. Thin, fixed-size chevrons have a separate gap before the frame marker and a straight terminal approach. Terminal lanes remain clear along their owning frame, preventing a route from doubling back behind its own marker on either input or return paths. Scrolling or moving a card updates its alignment; zoom alone does not move scene coordinates. Active routes avoid unrelated crossings; shared terminal junctions are allowed. Historical crossings use an underpass gap, and coincident historical trunks draw once instead of becoming a darker bus. Only actual corners are rounded; there are no artificial bridge bumps or connecting strokes over card content. Block interiors remain hard obstacles. Overlapping or truly occluded endpoints are flagged in connection details rather than routed through another block.
- **Call # versus event #**: `#1`, `#2`, etc. number tool-call starts and knowledge accesses across the entire request, not separately for each resource. Raw event numbers include starts, results and other telemetry; the timeline and details retain those separate event numbers.
- **Time evidence**: `+time` on a line is the selected call's recorded start offset from the request. Details show duration using `duration_ms` when reported, otherwise the difference between recorded start/end timestamps. Missing timestamps or completion events are not invented; replay does not expose a later completion on an earlier graph step.

During a running request, `REQUEST_MANIFEST` advances the live badge from document reading to **Waiting for model**; `MODEL_INFO` reports transport progress as **Working**. Subsequent captured tool calls show **Using tools**, and completion shows **Completed**. These statuses do not create calls or mark configured tools as used.

Usage counters count captured logical invocations, not individual start/result events. A call counts once per distinct matched resource and once per category even if several resources in that category match. Unused inventory does not become observed usage. Resource cards show their count beside existing call metadata; document list rows show a compact count, while bubble tooltips and the selected-document bar show the same value without changing captions or placement. Counters follow the displayed timeline position and reset for a new request route.

Used instrument cards in Commands, Tools, MCP/skills/integrations and Memory also have a footer invocation strip. One square represents one captured logical call, with at most 50 squares on a single row. If the count exceeds 50 or the available space beside existing footer controls, the strip becomes an amber band labelled `N+ calls`; its accessible label and tooltip retain the exact current count. Squares and the overflow band share the vertical centerline of the footer action buttons. The fill pulses gently while the text stays steady; reduced-motion preferences disable the pulse. This is invocation volume, not a success rate or completion-progress bar. Resizing recalculates the available space, and timeline rewind reduces or removes the strip without changing card dimensions or block positions.

Input/output figures come from usage events. Repeated reports sharing a `model_call_id` replace the earlier report for that call; independent calls are summed. Without usage identifiers Observer cannot tell whether reports from an external adapter are cumulative. Document tokens are a separate text estimate, not an additional charge added to provider usage. The input/output bar represents the reported token ratio, not a model context limit.

`max_runs: 0` keeps all saved routes; a positive value opts into retaining only that many recent routes. New installations default to keeping all routes. `max_steps` still bounds recorded steps within one route. Histories remain local and can contain user prompts and file paths. Restart Observer after changing server code or retention settings; refresh the browser for UI changes. CAD session/model fields require reopening the updated CAD Studio build after the current request finishes.


## Appearance

The sun/moon button in the header switches between light and dark themes. Light is the default. The selection is saved in the browser and applied before the page renders; it is shared by Observer tabs on the same origin. Both themes include graph routes, project/chat navigation, document shelves, logs, history, token counters and dialogs. Theme changes preserve graph positions, sizes, zoom and the selected route.
Block-header comment, information and settings controls share equal square targets, centered SVG icons and keyboard focus treatment. Related dialog actions use consistent typography and heights; text labels retain their natural widths. Block frames remain readable when zoomed out without changing block dimensions or wire endpoints; flat List/Bubbles controls indicate selection without a permanent outlined tile. Document search sits at the trailing edge beside the tree controls and keeps its width on focus, including compact layouts.

Action feedback uses one compact popover outside the panel layout. Routine connection and metadata updates use existing status badges instead of adding paragraphs beside controls. Brief confirmations dismiss automatically; errors remain until dismissed or another action clears them, without taking focus. Closing a dialog clears its feedback. Labels and empty states avoid repeated instructions and internal provenance; captured logs, recorded decisions, uncertainty labels and unsaved-draft protections remain intact. Memory settings keep equally sized action targets after the fields instead of overlaying them.

Dialogs and popovers use a short fade with a small entrance offset; Terminal and History fade in without moving their controls. Reduced-motion preferences disable these entrance animations. Pointer interaction does not add browser focus outlines or select toolbar text; keyboard focus remains visible and document/log text remains selectable. Translation opt-out metadata protects technical UI from automatic page translation, but a website cannot disable arbitrary third-party browser extensions.


## Resource details and Markdown editing

Click a command/tool card or a call-order entry to open the recorded operation: its purpose, category route, completion status, referenced files and captured result. Exact commands and original events remain available in expandable details. A missing completion is labelled as not recorded; a successful tool response does not prove the resulting artifact is correct. The information icon on a category opens its recorded calls or document list.

Click a Markdown document to open its complete **current project file** with formatted headings, tables and code. **Edit Markdown** offers source editing with a live preview; the pencil icon on a document card opens the editor directly. **Edit original file** also opens editing from the preview. Only **Save to original** (or Cmd/Ctrl+S) writes the original file at the path shown in the window; no export or separate copy is needed. Unsaved drafts survive closing the dialog for the current page lifetime. Reloading the page does not preserve them. Saving checks the loaded revision and keeps a backup under the configured storage directory in `document-backups/`. A conflicting disk change is rejected so the draft is not silently written over it. This viewer does not reconstruct historical document contents.

Document viewing and editing by the user do not create agent-access events. The local document service accepts registered Markdown documents inside the project or the explicitly selected vault, plus registered text input sources inside the project. Text inputs use **Edit Text** and escaped, preformatted previews rather than interpreting their contents as Markdown. Formatted Markdown previews disable raw HTML. Remote images appear as links, while supported local images are embedded only from the selected source root.

## Project-level block defaults

The rightmost settings button in each block header opens its controls. Titles remain on one line; activity and comments sit beside settings. **Save defaults** writes this installation's `config/observer.project.json`, preserving unrelated fields and keeping a revision backup in the configured storage directory. Concurrent or external changes reject stale saves instead of overwriting them. These settings are separate from browser-only layout and are not copied into other projects by source synchronization.

Settings fields and actions are locked during saves, MCP/skills rediscovery and confirmed document moves. Failed requests restore editing and retry actions without discarding the unsaved draft.

Memory systems, MCP & skills, and Commands keep fixed settings-window bounds and scroll internally; expanding groups and adding entries do not resize the window. Other block settings size to their content with bounded body scrolling. The short description sits directly below the title. Inputs share one control height. Bank filters start as a single line and grow only for additional IDs, up to four visible lines.

For CAD and generic project agents, Commands lists operations declared by runtime inventory or established by recorded calls, not a universal Codex/OMP command catalog. Before either source reports operations, an empty list is not a claim that the agent supports no tools. The number of cards is not an exhaustive installed-harness capability count.

- **Project prompt:** The connected agent's request events supply the original prompt, specification, attachments and refinement notes automatically. Source text is read-only. Colored source cards share one automatic height: longer input grows them together, while lengthy text scrolls within its card. Additional recorded specifications and refinements remain separate, equally sized source cards; attachments sit outside this equal-height grid. The block can be moved and resized using its edges; manual dimensions persist on reload and overflowing content remains scrollable. Comments remain separate annotations. This block does not register or scan source paths. Older `prompt_sources` settings no longer create input cards; existing project files are not deleted or modified.
- **Commands:** Select visible native agent operations, including custom operations actually registered by its runtime. A compact count shows visible commands; a leading dot identifies commands recorded in the current route. Header icons restore all commands or open recorded activity. The **?** button explains actions, visibility and layout one step at a time. Direct MCP calls belong to their server in MCP & skills; terminal programs such as OpenSCAD remain in Tools & services. A native Bash call that requests OpenSCAD can link both the Bash operation and the OpenSCAD service. Hiding a command card removes only its graph endpoint; Call order, block activity, exact arguments/results and history retain captured invocations. Completion-only records do not invent a start time.
- **Tools & services:** **Discover local tools** previews bounded local evidence from configured catalogs, executable locations and package scripts. It does not execute commands or connect to services. MCP server inventory belongs in MCP & skills, not a duplicate service card. Register selected candidates, add a descriptor manually, or copy the prepared discovery request into your agent chat and import its reviewed JSON inventory. Observer does not send that chat request itself. Entries derived from `tools_markdown` are changed by editing the original catalog.
- **Documents & references:** Configure project source folders/patterns or enter an explicit vault folder path. Internal and external vaults work in list and bubble views; `.obsidian` metadata is excluded and never modified. **Move file** keeps a registered document within its current project/vault root, requires confirmation and its current revision, and refuses overwrites, symlink sources and escaping destinations. Save or discard retained editor drafts first. A move outside existing document sources registers that file, not unrelated files in its destination folder. Markdown links and recorded history are not rewritten automatically.
- **Working files:** A flat, scrollable working set of file cards, without category headers. New files do not increase the block's height; manual Free-mode sizing remains available. Cards show the filename, full path and observed action/count; click for that file's recorded details. Only explicit command arguments establish access. Registered reference documents remain in Documents rather than appearing twice. Missing completion events remain **Called**, not **Used**; successful tool completion does not establish artifact correctness.

Resource activity opens in a compact dialog. Block details show a chronological list of all captured invocations, including commands hidden from the graph. The information button on a completed command opens that invocation directly, without the list. **Input** shows exact arguments, **Execution** shows observed status and captured intermediate logs, and **Output** shows the returned result and explicitly recorded destinations. A later command is chronology, not evidence that it received the result.

Selecting another call retains the chronological list and its scroll position, along with the raw-details expansion state. Live replacements refresh corrected timing, arguments and results; long histories are not truncated to make selection faster.

Block inspector counters use the block's actual resources; nested-category invocation summaries match each member's resource type. A used command, service or MCP endpoint is not reported as an empty category, and configured but unused endpoints are not counted as accessed.

The authenticated local API exposes `GET/POST /api/block-settings`, `GET /api/tools/discover`, `GET /api/capabilities`, `POST /api/capabilities/discover`, and `POST /api/document/move`. Document read/preview/save endpoints remain limited to registered document roots. The same token, Host and Origin checks protect all operations; no arbitrary-file or command-execution endpoint is introduced.

### Project-scoped feature settings

Each folder's **Project settings** gear opens that folder's inventory of all nine supported blocks and locally discoverable MCP servers, skills, CLI integrations, tools and known memory-provider locations. Opening settings never switches the current graph/session, changes its catalog, or connects an agent. The dialog identifies its target folder even while loading. Icons distinguish **Found locally**, saved **Visible in graph** preferences, and **Runtime connection**; another folder's preferences are never inferred from the current canvas. A configuration entry or executable on disk does not prove an active connection. Search uses bounded conventional project/agent locations and PATH; it does not scan the disk, launch servers, install memory or call a model.

The inventory is grouped by the graph's nine blocks, including declared native commands and registered documents/files. Each group has a visibility checkbox. Project prompt, Agent and Result are required; the six optional blocks can be hidden. Defaults are stored as revision-checked `hidden_blocks` in that project's configuration and apply across its sessions. Hidden blocks keep their evidence and counters but have no incoming/outgoing canvas arrows, obstacle geometry or Fit bounds.

Click a registered resource's **eye** to hide/show that individual document, working file, tool, MCP server, skill, integration or memory entry. Required prompt, agent and result entries cannot be hidden; discovery candidates must be registered first. A disabled parent block preserves individual choices and disables their controls until the block is enabled. Category counters show **visible / total** when resources are hidden, and hidden entries remain available in settings for restoring them.

Individual preferences are revision-checked and saved per project as `hidden_resources`; document/file keys use normalized paths so a catalog rescan cannot transfer a preference to a different file. Commands share the existing `hidden_commands` preference with block №5. Hidden resources retain their recorded calls and execution report. Their canvas endpoints, incoming/outgoing wires and bundled document routes are excluded rather than redirected to an empty folder or portal; hiding a document cannot stall bubble-route playback.


Repository role is a collapsed disclosure by default, leaving most dialog height to the independently scrolling inventory. Column and category headers stay pinned while rows scroll. Block categories use the canvas order and names, including **Documents & references**, and have independent expand/collapse arrows and visibility checkboxes. Controls follow the [WAI-ARIA disclosure pattern](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/). Expansion is remembered per folder while the interface is open. Search/filter expands matching groups without overwriting manual expansion choices. Header counters and primary rows follow the graph's registered resources, including hidden resources available for restoring and captured working files; additional discovery candidates remain in a separate **Not shown in this graph** disclosure, never counted as attached resources. Another folder uses its own authenticated catalog/evidence, not the current canvas.

The Documents header's **Obsidian** button opens an inline vault field with manual path entry, native **Choose folder…**, Save and Cancel. This shares the folder field/picker with block №3 rather than maintaining another configuration format. Both save the same revision-checked `vault_path` through `/api/block-settings`; selecting a folder alone does not save or switch projects. Foreign-folder saves remain scoped to that folder and refresh its inventory. Registered external vault notes are detected using their authoritative catalog paths. Disconnecting removes the registration, not the notes; `.obsidian` preferences are not changed.

Background inventory updates preserve vault drafts and caret position and defer DOM rebuilding during pointer/button-key gestures, so a refresh cannot swallow a Save/Cancel click. Invalid or stale saves keep the draft and display the existing configuration error.

Use a row's search icon or the toolbar search to retry discovery. Missing entries remain unmarked. Folder icons open the discovered configuration/tool folder in Finder; the toolbar folder opens the installed project's root. This explicit macOS action accepts a discovered entry ID, not an arbitrary path or shell command. Memory candidates still need registration in the Memory block; discovery does not silently change a provider or endpoint.

The authenticated `/api/project-settings` gateway accepts only saved project roots and a fixed operation allowlist. A running target graph must pass authenticated root and protocol checks before forwarding. Without a running target graph, its isolated configuration, discovery and role-specific session evidence remain available; a long-lived, folder-scoped service manager can run its trusted services and stream its logs without starting another graph. Owned workers retain their controller. Neither path substitutes the current project's resources. Late settings, service and log responses from a previously selected folder are discarded.

`GET/POST /api/graph-settings` reads/refreshes inventory. `POST /api/graph-settings/reveal` opens a known folder. All use the existing local authentication and Host/Origin checks.

### Local services and Terminal

The **Connection / Address** column shows the configured endpoint and live service health separately from discovery and recorded usage. A listening port alone is not proof: JSON services require recognized health data and configured identity; HTTP development sites additionally require a single same-user listener whose working directory belongs to the selected project. **External process** means started outside Observer; discovery and repeated Start do not transfer ownership. Skills and stdio MCP tools do not receive invented server controls.

Use the row's Start, Stop and Terminal icons. Start opens the associated tab in the separate **Terminal** workspace. Each service has its own read-only stdout/stderr view with bounded history, literal-text rendering and automatic following until you scroll up. This is a service log console, not an arbitrary shell. Closing a service tab does not stop it. The toolbar distinguishes managed and external processes. External stdout cannot be attached retroactively: only explicitly configured log files are shown.

Every folder heading has a Terminal shortcut. Running project localhosts automatically appear as ordinary console tabs, even without registered service definitions. A matching service and listener share one tab and the registered service's log source; commands, CWD, PID and origin appear in that console, not a separate process-list block. Closing a tab does not stop its process or reopen it on every status poll. A new running instance can open a tab again. Switching folders clears old panes/cursors and rejects late responses; **×** returns to the graph without stopping services.

Backend discovery still inspects same-user working directories, script evidence, ancestry and actual listeners. A terminal origin does not prove whether a person or an agent launched it. Protected Observer/controllers cannot be stopped; external Stop requires confirmation and a fresh identity/project/descendant check. External stdout cannot be recovered retroactively: available registered logs are shown, otherwise the console explicitly says that output is not attached. Light graph theme uses a light console, toolbar and tabs; dark mode remains dark, without replacing tabs or clearing logs.

Launch controls use trusted installation-local `local_services` definitions or bounded root `package.json` discovery. Conventional `dev`, `serve`, `start`, `preview`, `site`, `rss`, `refresh` and `monitor` scripts are available without executing them during scanning. Scripts without a known endpoint are process workers, not fake offline HTTP services. Actual listeners can supply verified loopback HTTP links and restart recipes matched to trusted npm scripts. HTTP/JSON definitions retain identity and project-CWD checks; browser requests cannot supply arbitrary commands or environment.

Clicking a name opens or selects its terminal log tab. A stopped, launchable service starts through its existing trusted service-ID action; an already running service shows a notification without another Start request, and a pending launch cannot be submitted twice. Unknown/unavailable status opens the log tab without claiming the service is running or starting it. Closing every tab leaves the name shortcuts available for reopening; it does not stop any service. The name list wraps on narrow windows instead of overflowing. Launch still uses fixed argv from the trusted definition/manifest; HTTP requests accept a service ID, not arbitrary commands, ports or environment. Display previews redact configured environment secrets and explicit credential arguments without changing the executable argv; credential helpers and environment values are not exposed. Older running Observer servers show command details as unavailable until restarted. Other launchers require explicit registration; a package declaration is not proof of a running server.

CAD registers one `cad-memory` service for Hindsight `8888` and taxonomy API `8877`. Its foreground launcher is `memory/.venv/bin/python memory/runtime.py serve`; the existing runtime manages PostgreSQL and shuts down its children. The trusted credential helper reads the CAD router key from Keychain only on an explicit Start; credentials are passed in memory, not returned in service snapshots. Configured environment credentials and structured secret fields are redacted before logs are buffered. Incomplete file-log records wait for their terminating newline; oversized records are omitted rather than exposing credential fragments.

CAD CLI `memory/runtime.py start/serve` also resolves the existing Keychain credential when the environment key is absent. Fatal startup returns a nonzero exit and visible sanitized diagnostics; unexpected worker exit is an error even when its exit code is zero. Prior file logs are labeled `(prior)` before the new Start marker, and each runtime launch has a session marker. Repeated Start does not transfer ownership of an external process or create another server.

Default graph-managed services stop with Observer through the guardian and parent-liveness pipe; external processes remain running unless explicitly stopped. **Start independently** requires confirmation, launches only registered argv and persists an identity record in private project state. That launch survives Observer shutdown and is rediscovered as independent, not mistaken for a newly owned external process. Closing a browser tab never stops a service. External terminal stdout cannot be recovered retroactively.

The authenticated API provides `GET /api/local-services` with services and process inventory, `POST /api/local-services/{id}/start` (optionally `{mode:"independent"}`), identity-checked service Stop, `POST /api/local-processes/{id}/stop` with `{identity}`, and cursor-based service logs. Existing token, Host, Origin and registered-project restrictions apply.


### Result files and previews

Result keeps an Agent-sized card with a quiet success tint and a single border instead of a doubled green frame. Published responses, prompts, refinements and historical prose share a local Markdown renderer: headings, emphasis, lists, task lists, tables, links, blockquotes and fenced code. Raw HTML is escaped, unsafe links are blocked, and remote images remain links rather than tracking requests. The pinned markdown-it 15.0.3 browser build and MIT license are bundled locally; rendering requires no CDN or external model.

Counts compare the first recorded baseline with the final captured text, not the sum of every intermediate edit. Reverted files disappear from the change list. An unknown baseline remains unknown, never a fabricated zero. OMP captures bounded text snapshots before mutation so queued telemetry cannot mistake already-written content for the original. Final assistant text excludes thinking blocks. Other adapters can attach `file_snapshots: [{"path":"src/example.py","stage":"before","exists":true,"content":"original text"}]` to a call start, and `stage: "after"` to its completion.

Opening Result shows the complete formatted response and a text-only execution report separated by a thin rule. Short reports size to their content; long responses use the dialog's full scroll surface rather than a 280px inner scroll box. File Changes / Before / After, captured model/image previews and original/captured links remain available below the report.

The report reuses the graph's recorded invocation sequence and ownership matching. It counts each paired start/end once, includes failures and missing completions, and lists counts per command, MCP server, skill, CLI integration, service, memory system and accessed document/file. Declared-only inventory does not count as use. Counters reflect the selected route at the selected timeline step; categories can overlap, so their subtotals are not a new global total. Recorded time span is not claimed as model runtime. File additions/deletions and line deltas use captured snapshots; absent baselines remain unreported, not zero. No extra commands run and no files are scanned to create this report.

Explicit `write/edit/apply_patch` paths, shell `-o/--output` flags, and final `artifacts: [{"path":"model.stl"}]` establish result candidates. Files must remain within the project; symlinks, private Observer storage and unobserved file IDs cannot be opened. Read-only access does not become a modification. Captures are local and immutable after completion; opening **Open file** reads the current original, while a deleted file opens its captured before-version. File access uses authenticated requests, not credentials in URLs.

STL and OBJ have an interactive local WebGL preview; captured PNG/JPEG/WebP outputs display directly. Other model formats can reuse an actually captured same-basename mesh/image companion. Missing/unsupported previews are identified rather than replaced with a fake model. Preview limits are 2 MiB per text snapshot, 32 MiB per artifact and 64 MiB per request capture. Result data lives beside request history under `results/`, not in the source bundle. `GET /api/results/{run_id}` and its scoped `/files/{file_id}` endpoint expose only recorded results.


### Resource card layouts

Commands, Tools & services, MCP & skills, and Working files each have a **Card layout** setting. Commands and MCP & skills use three keyboard-accessible icon choices in the footer beside **Reload settings** and **Save defaults**; Tools & services and Working files retain a compact select row. Layout changes apply with **Save defaults**:

| Mode | Behavior |
| --- | --- |
| List | One column; at most five visible card rows; vertical scrolling. |
| Grid | Responsive one to five columns; at most five visible rows; additional rows scroll. Widen the block to fit five columns. |
| Free | Drag any block corner to change width and height; cards reflow without the five-column limit. |

Offscreen linked cards use the existing top/bottom shelves, spanning the block width rather than retaining an empty left gutter. Shelves reserve space inside the block and preserve the recorded endpoints. Layout modes are project defaults; individual block sizes and positions remain browser-local. Documents retains its separate **List / Bubbles** buttons, which remain reachable when the navigation row wraps in a narrow block.

### Canvas camera and playback

The main canvas supports **15–180% zoom**. **Hand (H)** pans across block headers, cards and document content without moving the blocks; **Select (V)** restores header dragging and local document interactions. Buttons and editable fields remain interactive. Panning retains the reached camera position when capture is lost or the window loses focus. Saved camera coordinates survive catalog/Memory refreshes and window resizing; Fit and arrangement changes require explicit controls after initial loading.

**Arrange blocks** offers **Compact by size** and **Call order**. Compact packing allocates real block widths and heights from preferred content dimensions and readability minima, rather than moving unchanged boxes or shrinking their fonts. Its slicing floorplan tiles one rectangular envelope: adjacent blocks retain 96 graph-unit routing corridors, and Agent stays at the envelope's center. Hidden blocks do not participate. Band and column subdivisions support every combination of the three required blocks and six optional blocks. Arrangement is explicit; later telemetry does not continually move the graph. RU token captions are shortened while numeric values, estimate/provider distinctions and detailed tooltips remain available.

Preferred sizes account for visible inventory, request/source text and result length; empty inventories stay compact. The document frame reserves usable space for both List and Bubbles. Long content remains available through internal scrolling and the existing detail viewers. Assigned heights survive resource-layout updates and view switches.

Compact geometry is saved locally per project, repository role and session. Switching chats restores that chat's positions and dimensions; an untouched chat does not inherit another chat's packed layout. Reloading an explicit session restores its saved arrangement. Manual resize/move still works; Compact repacks the edited blocks, while Call order and Reset release packed sizing. Reapply Compact after changing the visible inventory instead of relying on continuous telemetry-driven rearrangement.

Only one canvas/document gesture owns the scene at a time. Competing pan, zoom, arrangement and resize actions do not change that gesture’s baseline. Visual route travel and its hold/dim delays pause during camera movement, in History and while the browser document is hidden, then resume without replaying delivered stages. Captured events and counters continue independently. Turning on reduced motion also stops completion glows already in progress. Canvas shortcuts do not run behind open dialogs; Activity arrow-key navigation retains focus across repeated call selections.

DEMO and live delivery preserve route graphics and card focus during background updates. Completed routes fade to faint trails rather than restarting opacity transitions. Arrival glows finish naturally across phase handoffs; repeated arrivals at an already glowing target share its current glow. Card outlines fade separately, after route arrival.

During canvas/document gestures and zoom bursts, received events enter live history immediately while heavy graph updates wait for motion to settle. The display keeps a stable timeline prefix, then catches up using every captured event. Explicit timeline selection cancels deferred tail-following. Call/document evidence is shared within each graph update rather than repeatedly rebuilding the same history for every resource.

Starting a block move/resize or rectangle selection takes keyboard focus from document/search controls, so Escape cancels the gesture rather than acting in the old field. Fullscreen transitions preserve the camera; the toolbar toggle and canvas Escape exit without fitting or rearranging the graph.

### Rectangle selection and group movement

Right-drag on empty graph canvas to draw a selection rectangle. Blocks intersecting the rectangle are outlined; release the right button, then left-drag any selected block's header to move the selection together. Relative positions are preserved, connections reroute, and completed positions are saved for this project in the browser. Header controls, document content and bubble interactions keep their existing behavior. Resizing still changes only the grabbed block. Escape cancels a block move/resize or selection rectangle, or clears the completed selection; a camera pan stops at its reached position. Clicking empty canvas in Select mode also clears the selection.

### Memory systems

**Memory systems** is separate from Commands, Tools & services and MCP & skills. Its settings register Hindsight connections or manually described memory systems. Memory cards use a scrolling list; old Grid/Free settings no longer reflow this block. Hindsight inspection uses authenticated, bounded read-only requests to configured loopback endpoints. Manual descriptions do not contact a service. This does not install memory, invoke an LLM, recall/retain/reflect, create banks, import legacy data or execute SQL.

OMP cards separate **Enabled/Disabled**, read-only **API available/unavailable**, and **observed activity**. A zero call counter is not proof that memory is off or empty. Only the selected backend is inspected; disabled alternatives are not probed. Reported bank facts and pending/failed operation counts remain bank metadata, not proof of successful retention by the selected session. Unreported category counts remain unknown; local capabilities do not pretend to have an API.

Project configuration uses `memory_systems`: stable `id`, `name`, `provider` (`hindsight` or `custom`), `endpoint`, optional `experience_endpoint` for CAD's taxonomy API, `bank_ids`, private `token_file` path, configured `categories`, `stores`, qualified `match.tool_names`, optional `github_url` and display-only `model` (`name`, `provider`, `base_url`). Empty Hindsight bank filters discover a bounded subset; explicit filters retain missing banks as **Not created**, not zero-filled statistics. Credential-bearing URLs and inline keys are rejected; token values and private auth paths are absent from public settings, catalog and metadata responses.

**Explain settings** (`?`) starts a field-by-field guide in memory block defaults and a memory card's Settings tab. One anchored popover explains the highlighted area; **Next / Back / Done** navigate without inserting text into the form or resizing the window. Escape closes the guide and returns focus to its help button. Only the advanced sections needed by the current step open temporarily; their previous state is restored when the guide ends. Guidance does not change configuration or unsaved drafts.

Use **+** beside **Configured memory systems** to choose **Hindsight** or **Manual system**. The adjacent activity icon opens recorded calls; each connection has a remove **×** in its upper corner. Additions and removals are staged until **Save defaults**. Removing a connection also removes its unused card from the live graph, without deleting the memory service or its data. Cards with recorded calls remain in the selected route for history inspection. The settings window keeps fixed bounds while advanced fields or additional connections scroll inside it.

The generated stable ID and chosen provider are read-only. Existing private authorization is shown as a dimmed status; `memory_auth_configured` reports only whether a private token-file reference exists, never its path or token, and does not prove that authentication succeeds. Unconfigured Hindsight connections can accept a token-file path, not a token value. API addresses, bank filters, display labels and fallback descriptors remain editable configuration, not automatically discovered runtime values. Model information is display-only metadata and does not reconfigure the memory service. Multiline bank filters and operation aliases use actual newline-separated entries.

Memory cards show the complete taxonomy hierarchy with each category's reported lesson count, including zero-valued categories. They open **Info / Logs / Settings**. Info puts the taxonomy counter and category details before bank/type/tag and storage details: unique lessons come from the experience API's `/health`, while category counts come from `/categories`. Successful API responses are authoritative; configured descriptors are only a fallback with unknown counts, not stale or zero-filled categories. Category assignments are not interchangeable with unique lessons; unavailable or partial totals stay unknown, not zero. Refreshing metadata does not create usage arrows. Logs shows captured memory operations, including input and output, across the matching explicit application/project/session; unlabelled legacy routes are not merged into a made-up session. Previous sessions without memory telemetry remain empty. Settings edits project metadata and memory inspection addresses, with a GitHub URL field and **Save settings**; repository/model descriptors persist without changing the underlying Hindsight inference setup. Drafts survive tab switches, stale revisions are rejected, other systems and private credentials are preserved. Retained reference databases are explained separately and excluded from the experience counter; Observer does not directly probe SQL or import their rows into Hindsight.

For CAD, Hindsight is `127.0.0.1:8888`, bank `cad-modeling`; taxonomy comes from the existing CAD experience API at `127.0.0.1:8877`. PostgreSQL `cad_memory` on `5547` holds CAD's `cad_experience` lesson schema and the separate Hindsight index. The preserved `docs/data/cad_standards.db` is explicitly legacy/reference data, not imported Hindsight memory.

The CAD installation registers only **Hindsight · CAD**. Independent memory services belonging to other agents are not added to this installation. Its card displays the configured API address directly; Info, per-system Settings, block defaults and CAD's Project settings show the same registered endpoint. The project feature search also matches addresses. A historical node's old endpoint does not override the current configured address, and displaying an address does not claim agent usage or reserve an operating-system port.


To record a memory operation, the emitter sends normal paired `TOOL_CALL_START` / `TOOL_CALL_END` events with immutable `run_id` and `call_id`, plus `memory_system_id`, `memory_bank_id`, `memory_operation` (`recall`, `retain`, `reflect` or `learn`) and the actual `memory_endpoint`. Contradictory endpoint/bank ownership is rejected; ambiguous matches do not create memory-use edges. A generic `write xd://retain` alone does not identify CAD's memory. Existing uninstrumented calls and past histories are not retroactively attributed; acceptance/queueing must not be reported as persisted retention.

### Animated demo

Choose **D** to open a separate window with a synthetic CAD catalog and randomized, size-aware arrangement. It demonstrates all nine blocks: prompt, knowledge and skill reads, simulated memory recall/retention, tools and commands, working files, failed verification, correction, MCP inspection and a successful result with captured source/diff/mesh previews and a textual execution report. The synthetic inventory is clearly marked DEMO and is not presented as the current project's installed tools.

Calls are spaced 3.6 seconds apart and complete 2.8 seconds after starting; visual routing can continue after capture ends. The child uses the same docking, highlighting, timeline selection and fading as live observation. **Restart demo** starts the scenario again with a new arrangement. It has a separate storage namespace, no real project bootstrap/history/credential, no live WebSocket and no project API traffic; project mutations are disabled. Parent sessions, layout, camera, services and history remain untouched. Closing the child returns to the unchanged working window.

All demo events stay in the browser. They do not execute commands, connect MCP tools, create project files, call a memory API or enter saved request history. **DEMO ONLY** results must not be interpreted as evidence of actual tool use or persisted memory.

Run `node --test src/agent_observer/tests/demo-regression.test.mjs` for isolated real-Chrome behavior coverage, including Markdown safety, report accounting, scoped settings, late console-response rejection, hidden-block routing and centered mandatory-only packing. Run `PYTHONPATH=src .venv/bin/python -m unittest discover -s src/agent_observer/tests -p test_session_projects.py` for saved-folder isolation, revisioned visibility and real folder-scoped service start/log/stop checks. `test_local_services.py` also verifies HTTP listener CWD ownership and guardian lifecycle. Fixtures do not access CAD history or real project services.


### MCP, skills and OMP

Connect an existing OMP project from the canonical Observer repository:

```bash
.venv/bin/python observer.py connect-omp "/path/to/project"
```

For a new installation, use `observer.py install "/path/to/project" --agent omp`. This creates a metadata-only `config/observer.capabilities.json` in that installation and an owned `.omp/extensions/agent-observer.ts` loader. Unowned extension files are preserved. Start a new OMP session to load the extension; refreshing Observer does not inject an extension into a running agent.

The separate **MCP & skills** block distinguishes configured MCP servers, their runtime connection states, provided skills, and CLI integrations. Installed/discovered skills are not automatically labelled provided or read. A skill read is evidence of access to its files, not proof that its guidance was applied. RTK is a CLI wrapper, not an MCP server: its availability is separate from a recorded shell prefix. Actual `rtk` executable prefixes are colored in captured command inputs; quoted mentions such as `echo 'rtk ...'` are not treated as wrapper calls.

Discovery follows the selected connection, not the model router. For Codex, the installed CLI's read-only `mcp list --json` supplies the effective server list, including built-in registrations missing from `config.toml`; readable project/global Codex configuration supplies metadata and is the fallback when that CLI query is unavailable. Disabled entries remain disabled. This query neither launches MCP servers nor calls a model. A configured entry is not labelled connected until runtime evidence reports that state.

MCP settings group automatic inventory into collapsible server, skill and CLI lists with counts. Detected rows are read-only; empty categories remain compact. Header icons rediscover inventory, open recorded activity and add a manual **MCP server** or **Skill** through the **+** menu. Manual entries share one section, with a corner **×** to remove each registration. Removing one entry preserves edits in the others. **?** opens sequential anchored help, ending at the footer layout choices; it does not change the draft. Manual registration describes a graph endpoint, not an installation or connection.

Use the pin button on an MCP or skill card to keep it at the top of the block in List, Grid or Free mode. Click again to unpin and restore its normal catalog position; Enter and Space also toggle the control. Pins survive catalog refreshes, live updates and page reloads, and are saved per project in this browser, separately from shared project defaults. Pinning changes presentation only: it does not connect an MCP server, provide or apply a skill, record an invocation, or change connection endpoints.

Open an MCP, skill or CLI integration card to view **Details**: purpose, description source, repository and reported connection/availability or skill path. **Activity** remains a separate tab. CLI integration details are read-only. For MCP and skill cards, **GitHub links** accepts one HTTPS GitHub or GitHub Gist URL per line; choose **Save links** to retain them. Saved links open in another browser tab; saving does not fetch the repository, install anything or record agent activity.

Purpose descriptions prefer explicit project text, then known repository/package metadata or an Observer-authored description of the capability. Manual text survives inventory rediscovery and runtime updates. Optional runtime `tools: [{"name": "...", "description": "..."}]` definitions retain the provider's operation descriptions in Details. Reference descriptions enrich only declared operations: knowing a project's documentation never registers its tools, establishes a connection, or creates usage arrows.

Links are project metadata stored in `config/observer.project.json` under `capability_links`, keyed by the stable MCP/skill resource ID. They survive page reloads, installation synchronization and inventory rediscovery. Saving one resource does not replace another resource's links; clearing the field removes that resource's saved links. Drafts survive tab switches and closing/reopening the details window within the same page. Concurrent configuration changes reject stale saves without discarding the draft; **Reload saved links** explicitly reloads the saved values. URLs with non-GitHub hosts, non-HTTPS schemes or embedded credentials are rejected.

The OMP adapter reads the existing `MCPManager.instance()` and the current session's `AgentRegistry` skills, without creating another agent session or making a model request. Exact runtime tool ownership or MCP result metadata identifies server endpoints; arbitrary MCP names are not split to guess an owner. Tool-execution events preserve arguments after middleware rewrites, including RTK.

Token-saving integrations show a live-updating **out / without** counter below their title. A tool may report `token_metrics: {"output_tokens":200,"without_tokens":1200,"estimated":true,"source":"tool-report"}` on completion. RTK's read-only history is matched by project, exact joined arguments and completion time; ambiguous or already-attributed rows are not counted twice. These are RTK's estimates, not model-provider billing tokens. Missing metrics can show a clearly marked output character estimate, but never invented savings. Partial baseline coverage is not compared against a larger output total.

Capability Details includes expandable **Recorded executions** beneath its metadata and GitHub links: command/input, captured output and latest output. Explicit `nested_calls` or provider `result.details.tool_calls` can show recorded internal operations and their individual results. Observer does not infer hidden server commands from a tool's purpose. Expansion state survives refreshed details. The full chronological Activity tab remains separate.


**Search MCP & skills again** refreshes the selected agent's passive installation inventory without launching or connecting MCP servers, invoking a model, resetting a live route, or replacing runtime connection evidence. Skill discovery includes that agent's standard skill folders, a root `SKILL.md`, and directly stored Markdown skill definitions with named frontmatter; it does not scan `docs/skills` as a plugin folder. Obsolete discovery caches are ignored so old document classifications cannot return on reload. Manual MCP and skill registrations are Observer catalog descriptors only; they do not install, connect, or provide a capability to the agent. OMP refreshes its reported inventory on session changes and before a request starts.

For other agents, send `RUNTIME_INVENTORY` with an explicit `session_id` before `PROMPT_START` and on capability changes:

```json
{
  "type": "RUNTIME_INVENTORY",
  "session_id": "my-session",
  "inventory": {
    "source": "agent-runtime",
    "mcp": [{"name": "design-library", "connection": "connected", "tool_names": ["design_lookup"], "tools": [{"name": "design_lookup", "description": "Look up approved CAD design references."}], "description": "Project CAD design-reference service.", "description_source": "connected-metadata"}],
    "skills": [{"name": "cad-standards", "path": "/project/skills/cad-standards/SKILL.md", "availability": "provided"}],
    "integrations": [{"name": "rtk", "type": "cli-wrapper", "availability": "available"}],
    "native_tools": [{"name": "read", "source": "builtin"}, {"name": "bash", "source": "builtin"}]
  }
}
```

Use the same session ID on request/tool events. Inventory metadata alone creates no agent-use arrows. Direct calls use exact `tool_names`; native `write` to an exact `xd://<tool-name>` can link its MCP owner, while `read` of that URI only loads schema. An explicit `mcp_server` on a recorded result can establish ownership even when connection failed; failure does not establish a connection. Inventories are session-scoped and retained with route history.

## Local security and release verification

Observer is a loopback-only tool for a trusted, single-user workstation, not a multi-user security boundary. The dashboard supplies its local API credential to the local browser. Do not expose its listener through a public proxy.

Dashboard and default responses refuse framing through `frame-ancestors 'none'` and `X-Frame-Options: DENY`. Result capture opens candidate files nonblocking and accepts only regular files, so a named pipe cannot hang capture while waiting for a writer.

Project visibility saves synchronize both preference and catalog metadata for foreign folders. Memory save shortcuts reject invalid category/storage JSON, and a completed memory-resource save clears only the draft actually submitted; later edits remain available. Inventory search includes translated interface/type labels while preserving technical resource names.

The bundled browser renderer exactly matches `package/dist/browser/markdown-it.umd.min.js` in the [markdown-it 15.0.3 npm archive](https://registry.npmjs.org/markdown-it/-/markdown-it-15.0.3.tgz), verified against the [registry metadata](https://registry.npmjs.org/markdown-it/15.0.3). SHA-256 of the vendored file: `391f2aa638e75e6c7859d0a5c5650ef0956722a45998f17e05b83bc655778e1e`. Its MIT notice is preserved in `src/agent_observer/web/markdown-it.LICENSE`. This does not assign a license to Agent Observer itself.

Keep `.agent-observer/`, `.observer/`, `var/`, virtual environments, token files, histories and local backups out of distributions. Build release archives from canonical source paths rather than zipping the whole working directory.

## External OMP observation

In **Agent connections**, choose **Working repository**, then **OMP**, **Analyze folder**, and **Generate graph**. Codex remains a separate choice. The equivalent CLI selection is:

```bash
python observer.py connect "/path/to/project" --module omp
```

OMP observation reads only new committed records in matching main JSONL v3 journals. Matching includes the session's primary directory and explicitly added workspace directories, so a session started elsewhere can still belong to this project. Existing conversation is not imported on attachment; a captured mid-turn route has no invented old request. Observer namespaces OMP sessions separately from Codex.

The project-local `.omp/extensions/agent-observer.ts` supplies richer live inventories on a new OMP launch from the project directory. It does not retroactively load into an already-running session. For a persisted main session the journal is authoritative for lifecycle and the extension adds inventory; ephemeral sessions without a matching journal can use extension lifecycle.

The Commands block separates version-labelled OMP command references from captured tool use. References do not run on click. Memory cards distinguish enabled settings, disabled alternatives and actually observed activity. Hindsight API health, successful recall and completed retention are different claims; no model, router, memory backend or credential changes are made by connecting.

Internal `eval` helper calls, automatic memory processing and TUI-only operations are not fully represented unless OMP emits their own structured events. Local development notes and diagnostic reports are not included in distributions.

After synchronization, an older running Observer server needs a manual restart to load new Python routes; refreshing its browser assets alone cannot add OMP module support. Existing user processes are never forcibly restarted by synchronization.

Synchronization also adopts an already identical newly managed file, such as `config/omp-reference.json`, into the installation manifest without copying it. This prevents a false local-edit warning and allows later ordinary updates. Actual local edits still block synchronization and are never forcibly overwritten.

## Multiple agent sessions in one folder

A **Working repository** now discovers and passively observes both Codex and OMP, regardless of which is selected for initial setup. The sidebar keeps one folder group and adds a small `codex` or `OMP` label at the right of each session. The embedded **Project agent** role stays separate.

OMP IDs retain the `omp:` namespace. Each session opens its own history, result and source-specific resource catalog. Native discovery is merged with live metadata; a server returning only one source cannot remove the other source's native sessions. Unknown agent types are not labelled Codex by default.

Dashboard protocol is now 13. Existing running servers need a restart to load the dual-reader backend and session-observation routes/assets; a page refresh alone cannot add Python routes. Synchronization does not restart user services.

## Persistent command connections

Once a block connection has arrived, subsequent calls reuse its arrow rather than hiding and revealing it again. The label stays visible and the counter accumulates calls to that block pair.

An arrow pulses while at least one recorded call on the connection remains unfinished. Completing one of several parallel calls does not stop the pulse; completing the last call or ending the run does. Completed connections remain visible as muted history. Reduced-motion preferences keep the active arrow static, and the existing motion pause control pauses the pulse.

## Command cards while scrolling

Used commands compact only after fully leaving the scroll viewport. Commands already scrolled past appear in the upper shelf; commands below the viewport appear in the lower shelf. Each side shows at most two compact commands. Only when that side contains more than two does a **hidden / View all** row count the excess. At the very top there is no upper shelf; at the very bottom there is no lower shelf. Partially visible cards are never blanked.

Compact rows use a short 140 ms opacity-only appearance, without large-card clones, scaling or geometry animation. Scrolling back restores the full cards. Reduced motion skips animation and motion pause pauses it. The shared scrollbar includes both shelves; its visible thumb is 4 px wide while the input target remains 8 px.

External connections keep stable group-side ports, so Commands scrolling does not displace Working files routes. A local branch rail connects the Commands entry point to visible command representations. Full and compact commands show their call-number range and count; the trunk keeps its aggregate label.

Opening a command with multiple recorded calls shows the complete captured chronological list alongside the selected call's arguments, result and status. Click another call or use Arrow Up/Down to inspect it. A command with only one captured call keeps its compact detail pane.

## Session activity indicator

Running sessions gently pulse the letters of their **OMP** or **codex** source tag, without an additional activity dot. Sessions with no source tag, including embedded CAD sessions, retain the pulsing dot. Completion stops the pulse; reduced-motion preferences leave a static activity indication. Waiting/error indicators and session-loading spinners keep their existing behavior.

## Agent setup cards

Working-repository setup lists Codex and **OMP — Oh My Pi** as equal-height cards rather than a horizontal agent selector. Analyze the desired card before generating its graph; another agent's analysis does not enable that card's Generate button.

The lower-right GitHub icon stays aligned with **Generate graph**, including when actions wrap. OMP opens its [official repository](https://github.com/can1357/oh-my-pi) in a new tab. Codex and other agents without an assigned link display a muted, inactive icon.

Analysis now appears directly under the card that requested it, sharing its outline. The upper-right chevron hides or reveals that report with a 260 ms height/fade transition; reduced-motion preferences make it immediate. Each agent keeps its own report and disclosure state while another agent is analyzed. **Analyze again** replaces and reopens only that report rather than appending a duplicate. Changing folders or roles clears the old reports. Summary cards remain equal-height independently of their expanded reports.

## Live agent session information

**Live agent log** has keyboard-accessible **Log**, **/commands**, **/model** and **/session** tabs. Slash-labelled cards describe observed queues, todo/plan progress, background jobs, approvals, compaction/handoff, retry and memory activity; they are not command-launch buttons. This integration is observation-only.

The context ribbon shows occupied tokens and capacity when reported. At 85% it displays an amber warning; at 95% the narrow ribbon becomes urgent, without turning the whole block red. Automatic compaction is observed; without a fresh post-compaction measurement the old full percentage is cleared. Journal-derived provider usage is marked approximate, not presented as the live TUI gauge or cumulative token spend.

An existing OMP session receives public journal metadata without importing its old conversation. Runtime-only jobs, pending-message state and more precise context require the updated extension to be loaded by OMP. The TUI extension API exposes a pending boolean, not a detailed queue roster: unavailable state is labelled **Not exposed**, never a fabricated zero. Codex journal observations follow the same unavailable-state policy. No agent process is restarted and no `/compact` or `/handoff` is sent.
