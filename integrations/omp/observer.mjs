// Agent Observer Extension for Oh-My-Pi (OMP)
// Connects OMP runtime to local Agent Observer visualizer.
// No blocking network requests or input/result changes. Bounded before/after file snapshots preserve actual changes.

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

function captureFileSnapshots(toolName, args, cwd, stage) {
  const name = String(toolName || "").split(".").at(-1);
  if (!["write", "edit", "apply_patch", "create_file", "write_file", "edit_file", "bash", "shell", "exec_command"].includes(name)) return [];
  let input = args;
  if (typeof input === "string") { try { input = JSON.parse(input); } catch { input = { patch: input }; } }
  if (!input || typeof input !== "object") return [];
  const paths = new Set();
  if (typeof (input.path || input.file_path) === "string") paths.add(input.path || input.file_path);
  for (const line of String(input.patch || input.content || "").split("\n")) {
    const match = line.match(/^\[(.+)#[\dA-Fa-f]{4}\]$/) || line.match(/^\*\*\* (?:Update|Add|Delete) File: (.+)$/);
    if (match) paths.add(match[1]);
  }
  if (typeof input.command === "string") {
    for (const match of input.command.matchAll(/(?:^|\s)(?:-o|--output)(?:=|\s+)("[^"]*"|'[^']*'|[^\s;&|]+)/g)) paths.add(match[1].replace(/^(['"])(.*)\1$/, "$2"));
  }
  let root;
  try { root = fs.realpathSync(cwd || process.cwd()); } catch { return []; }
  const snapshots = [];
  let bytes = 0;
  for (const raw of [...paths].slice(0, 32)) {
    const absolute = path.resolve(root, raw), relative = path.relative(root, absolute);
    if (!relative || relative.startsWith(".." + path.sep) || path.isAbsolute(relative) || [".git", ".agent-observer"].includes(relative.split(path.sep)[0])) continue;
    try {
      let current = root;
      for (const part of relative.split(path.sep)) { current = path.join(current, part); if (fs.lstatSync(current).isSymbolicLink()) throw new Error("symlink"); }
      const info = fs.statSync(absolute);
      if (!info.isFile() || info.size > 2 * 1024 * 1024 || bytes + info.size > 4 * 1024 * 1024) continue;
      const data = fs.readFileSync(absolute);
      const content = new TextDecoder("utf-8", { fatal: true }).decode(data);
      if (content.includes("\0")) continue;
      bytes += data.byteLength; snapshots.push({ path: raw, stage, exists: true, content });
    } catch (error) {
      if (error.code === "ENOENT") snapshots.push({ path: raw, stage, exists: false });
    }
  }
  return snapshots;
}

function loadInstallationConfigInfo(cwd) {
  if (process.env.AGENT_OBSERVER_CONFIG && fs.existsSync(process.env.AGENT_OBSERVER_CONFIG)) {
    try {
      const cfgPath = path.resolve(process.env.AGENT_OBSERVER_CONFIG);
      return { config: JSON.parse(fs.readFileSync(cfgPath, "utf-8")), configPath: cfgPath };
    } catch {}
  }
  try {
    const currentFile = fileURLToPath(import.meta.url);
    const candidate = path.resolve(path.dirname(currentFile), "../../config/observer.project.json");
    if (fs.existsSync(candidate)) {
      return { config: JSON.parse(fs.readFileSync(candidate, "utf-8")), configPath: candidate };
    }
  } catch {}
  if (cwd) {
    const inCwd = path.resolve(cwd, ".agent-observer/config/observer.project.json");
    if (fs.existsSync(inCwd)) {
      try {
        return { config: JSON.parse(fs.readFileSync(inCwd, "utf-8")), configPath: inCwd };
      } catch {}
    }
  }
  return { config: null, configPath: null };
}

function resolveToken(cwd) {
  if (process.env.AGENT_OBSERVER_TOKEN) return process.env.AGENT_OBSERVER_TOKEN.trim();
  if (process.env.CAD_XRAY_TOKEN) return process.env.CAD_XRAY_TOKEN.trim();

  const fileCandidate = process.env.AGENT_OBSERVER_TOKEN_FILE || process.env.CAD_XRAY_TOKEN_FILE;
  if (fileCandidate && fs.existsSync(fileCandidate)) {
    try {
      return fs.readFileSync(fileCandidate, "utf-8").trim();
    } catch {}
  }

  const isDarwin = process.platform === "darwin";
  const supportDir = isDarwin
    ? path.join(os.homedir(), "Library/Application Support")
    : path.join(os.homedir(), ".local/share");

  const { config, configPath } = loadInstallationConfigInfo(cwd);
  if (config) {
    const profile = config.storage_profile || "project";
    if (profile === "cad-studio") {
      const cadPath = path.join(supportDir, "CADStudio", "xray-token");
      if (fs.existsSync(cadPath)) {
        try {
          return fs.readFileSync(cadPath, "utf-8").trim();
        } catch {}
      }
      return "";
    }

    let projectRoot = cwd ? path.resolve(cwd) : process.cwd();
    if (config.project?.root && configPath) {
      projectRoot = path.resolve(path.dirname(configPath), config.project.root);
    }
    const identity = crypto.createHash("sha256").update(projectRoot).digest("hex").slice(0, 12);
    const idPath = path.join(supportDir, "AgentObserver", identity, "xray-token");
    if (fs.existsSync(idPath)) {
      try {
        return fs.readFileSync(idPath, "utf-8").trim();
      } catch {}
    }
    return "";
  }

  const base = cwd ? path.resolve(cwd) : process.cwd();
  const identity = crypto.createHash("sha256").update(base).digest("hex").slice(0, 12);
  const idPath = path.join(supportDir, "AgentObserver", identity, "xray-token");
  if (fs.existsSync(idPath)) {
    try {
      return fs.readFileSync(idPath, "utf-8").trim();
    } catch {}
  }
  return "";
}

function resolveEndpoint(cwd) {
  if (process.env.AGENT_OBSERVER_ENDPOINT) return process.env.AGENT_OBSERVER_ENDPOINT.trim();
  if (process.env.AGENT_XRAY_ENDPOINT) return process.env.AGENT_XRAY_ENDPOINT.trim();
  if (process.env.AGENT_OBSERVER_PORT) return `http://127.0.0.1:${process.env.AGENT_OBSERVER_PORT.trim()}/api/event`;

  const { config } = loadInstallationConfigInfo(cwd);
  const port = config?.port || 8766;
  return `http://127.0.0.1:${port}/api/event`;
}

async function memoryInventory(pi, ctx) {
  let backend;
  try { backend = (await ctx?.memory?.status?.())?.backend; } catch {}
  let lookup;
  // This host-bundled module exists inside OMP, but not in the standalone Node adapter harness.
  try { ({ lookup } = await import("@oh-my-pi/pi-coding-agent/config/registry")); } catch {}
  const setting = (key, fallback) => {
    try { return lookup?.(key)?.get(pi.pi?.settings) ?? fallback; } catch { return fallback; }
  };
  backend = setting("memory.backend", backend);
  if (!["off", "hindsight", "local", "mnemopi", "sharpshooter"].includes(backend)) return null;
  return [
    ...[
      ["hindsight", "Hindsight", ["retain", "recall", "reflect", "learn"]],
      ["local", "Local summaries", ["learn"]],
      ["mnemopi", "Mnemopi SQLite", ["retain", "recall", "reflect", "memory_edit", "learn"]],
      ["sharpshooter", "Sharpshooter decisions", []],
    ].map(([id, name, tools]) => {
      const enabled = backend === id;
      return { id: `omp-${id}`, name: `OMP · ${name}`, provider: "custom", endpoint: "",
        enabled, availability: enabled ? "configured" : "unavailable", source: "omp-runtime",
        description: `${enabled ? "Selected" : "Not selected"} OMP backend. Configuration is not proof of health or completed retention.`,
        match: { tool_names: enabled ? tools : [] } };
    }),
    ...[
      ["managed-skills", "Managed skills / Auto-learn", setting("autolearn.enabled", false), ["manage_skill"]],
      ["archive", "Read-only session archive", setting("archive.enabled", true), []],
    ].map(([id, name, enabled, tools]) => ({id:`omp-${id}`, name:`OMP · ${name}`, provider:"custom", endpoint:"",
      enabled:Boolean(enabled), availability:enabled?"configured":"unavailable", source:"omp-runtime",
      description:"Independent OMP capability, not an additional selected memory backend.", match:{tool_names:enabled?tools:[]}})),
  ];
}

function referenceCommands() {
  try {
    return JSON.parse(fs.readFileSync(new URL("../../config/omp-reference.json", import.meta.url), "utf8")).commands;
  } catch { return []; }
}

export default async function agentObserverExtension(pi) {
  let endpoint = resolveEndpoint();
  let token = "";
  let sessionId = `omp:${process.pid}`;
  let currentRunId = null;
  let rtkAvailable = null; // Checked once at startup
  let latestCtx = null;
  let activeMemoryId = null;
  const activeCalls = new Map();
  const unsubscribers = [];
  let observationTimer = null, timerContext = null, observationOwner = null, observationSignature = "";
  function emitObservation(ctx, patch = {}) {
    if (!ctx || ctx.agent?.kind === "sub" || ctx !== latestCtx) return;
    const owner = sessionId, observation = {
      session: {id: owner, name: pi.getSessionName?.(), cwd: ctx.cwd, application: "OMP"},
      ...patch,
    };
    try {
      const model = ctx.model;
      if (model) observation.model = {id: model.provider && !String(model.id).includes("/") ? `${model.provider}/${model.id}` : model.id,
        provider: model.provider, context_window: model.contextWindow, effort: pi.getThinkingLevel?.()};
      const usage = ctx.getContextUsage?.();
      if (usage && typeof usage.tokens === "number") observation.context = {
        tokens: usage.tokens, window: usage.contextWindow || model?.contextWindow,
        percent: usage.percent, source: "runtime", estimated: false,
      };
      else if (patch.compaction?.status === "completed") observation.context = {
        tokens: patch.compaction.tokens_after ?? null, percent: null, window: model?.contextWindow,
        source: patch.compaction.tokens_after == null ? "awaiting_post_compaction_usage" : "compaction", estimated: true,
      };
      const pending = ctx.hasPendingMessages?.();
      observation.queue = {available: false, ...(typeof pending === "boolean" ? {pending} : {})};
      const modes = ctx.sessionModes?.getState?.();
      if (modes) observation.mode = Object.fromEntries(["plan", "goal", "vibe", "loop"].filter(key => modes[key] != null).map(key => [key, modes[key]]));
      const jobs = ctx.getAsyncJobSnapshot?.();
      const items = Array.isArray(jobs) ? jobs : Array.isArray(jobs?.jobs) ? jobs.jobs : Array.isArray(jobs?.items) ? jobs.items : null;
      if (items) observation.jobs = {available: true, items: items.slice(0, 100).map(job => ({
        id: job.id, name: job.name || job.label, status: job.status || job.state, description: job.description,
      }))};
      const review = ctx.getPendingPlanReview?.();
      if (review) observation.approval = {status: "pending", kind: "plan", title: review.title};
    } catch { /* Unsupported optional metadata must not affect the session. */ }
    if (owner !== sessionId || ctx !== latestCtx) return;
    const signature = JSON.stringify(observation);
    if (signature === observationSignature) return;
    observationSignature = signature;
    if (observation.context) observation.context.observed_at = new Date().toISOString();
    enqueueEvent({type: "SESSION_OBSERVATION", session_id: owner, observation, cwd: ctx.cwd});
  }
  function observeContext(ctx) {
    if (observationTimer != null) timerContext?.clearTimer?.(observationTimer);
    observationTimer = null; timerContext = ctx; observationOwner = sessionId; observationSignature = "";
    emitObservation(ctx);
    if (ctx?.setInterval && ctx.agent?.kind !== "sub") {
      const owner = observationOwner;
      observationTimer = ctx.setInterval(() => { if (owner === observationOwner && owner === sessionId && latestCtx === ctx) emitObservation(ctx); }, 1000);
    }
  }

  // Telemetry queue: ordered, non-blocking, bounded FIFO
  const telemetryQueue = [];
  let isDraining = false;
  const MAX_QUEUE = 512;

  function enqueueEvent(payload) {
    if (["TOOL_CALL_START", "TOOL_CALL_END"].includes(payload.type) && !payload.mcp_server && mcpManager) {
      const args = payload.args || payload.input;
      const alias = payload.tool_name === "write" ? /^xd:\/\/([^/?#:]+)/.exec(String(args?.path || ""))?.[1] : payload.tool_name;
      if (String(alias || "").startsWith("mcp__")) {
        const owners = (mcpManager.getTools() || []).filter(tool => tool.name === alias && typeof tool.mcpServerName === "string");
        if (owners.length && owners.every(tool => tool.mcpServerName === owners[0].mcpServerName)) {
          payload.mcp_server = owners[0].mcpServerName;
          payload.mcp_tool = alias;
        }
      }
    }
    payload = {application: "OMP", source: "omp", capture_mode: "extension", ...payload};
    if (activeMemoryId && ["TOOL_CALL_START", "TOOL_CALL_END"].includes(payload.type)) {
      const name = String(payload.tool_name || "").replace(/^functions\./, "");
      const args = payload.args || payload.input;
      const operation = /^(recall|retain|reflect|learn)$/.exec(name)?.[1]
        || (name === "write" && /^xd:\/\/(recall|retain|reflect|learn)(?:$|[/?#])/.exec(String(args?.path || ""))?.[1]);
      if (operation) { payload.memory_operation = operation; payload.memory_system_id = activeMemoryId; }
    }
    const previous = telemetryQueue.at(-1);
    if (payload.type === "RUNTIME_INVENTORY" && previous?.type === payload.type && previous.session_id === payload.session_id) {
      telemetryQueue[telemetryQueue.length - 1] = payload;
      return;
    }
    if (telemetryQueue.length >= MAX_QUEUE) {
      const dropIdx = telemetryQueue.findIndex(
        (e) => e.type !== "RUNTIME_INVENTORY" && e.type !== "PROMPT_START" && e.type !== "GENERATION_END"
      );
      if (dropIdx !== -1) telemetryQueue.splice(dropIdx, 1);
      else telemetryQueue.shift();
    }
    telemetryQueue.push(payload);
    void drainTelemetry();
  }

  async function drainTelemetry() {
    if (isDraining) return;
    isDraining = true;
    try {
      while (telemetryQueue.length > 0) {
        const item = telemetryQueue.shift();
        await sendPayload(item);
      }
    } finally {
      isDraining = false;
    }
  }

  async function sendPayload(payload) {
    if (!token && payload.cwd) token = resolveToken(payload.cwd);
    if (!token) token = resolveToken(process.cwd());
    if (!token) return;

    try {
      const headers = {
        "Content-Type": "application/json",
        "X-Agent-Observer-Token": token,
        "X-CAD-Token": token,
      };
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 800);
      await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        signal: controller.signal,
      }).catch(() => {});
      clearTimeout(timer);
    } catch {
      // Non-blocking telemetry; failures must never interrupt the agent.
    }
  }

  const NATIVE_TOOL_DESCRIPTIONS = {
    read: "Read local path, internal URI, or URL with line selectors",
    write: "Create or overwrite file atomically",
    edit: "Hashline patch existing file with snapshot validation",
    eval: "Evaluate Python or JavaScript code directly in the runtime kernel",
    bash: "Command execution shell",
    glob: "Recursive file and directory pattern discovery",
    grep: "Regex content pattern search",
    task: "Subagent task delegation",
    wait: "Wait on background tasks and subagent deliveries",
    web_search: "Search web and documentation with citations",
    apply_patch: "Apply structured patch to files",
    read_image: "Inspect image dimensions, format, and visual contents",
    view_image: "Inspect image dimensions, format, and visual contents",
    browser: "Drive headless Chromium browser for navigation and interaction",
    screenshot: "Capture screen or application window",
    transcribe: "Extract speech to text from audio recordings",
  };

  const KNOWN_MCP_SERVERS = {
    "codebase-memory-mcp": {
      description: "Persistent codebase knowledge graph maintaining AST symbol indices, call hierarchies, and architectural dependencies across sessions.",
      github_url: "https://github.com/DeusData/codebase-memory-mcp",
      description_source: "github",
    },
    codegraph: {
      description: "Local SQLite-backed code intelligence for symbol search, call hierarchy traversal, and change-impact analysis.",
      github_url: "https://github.com/colbymchenry/codegraph",
      description_source: "package",
    },
    jcodemunch: {
      description: "Token-efficient source code exploration server using tree-sitter AST parsing for symbol navigation, outline indexing, and catalog dispatch.",
      github_url: "https://github.com/jgravelle/jcodemunch-mcp",
      description_source: "package",
    },
    xcodebuildmcp: {
      description: "Builds and tests Apple projects, manages simulators, and exposes app inspection and UI automation tools.",
      github_url: "https://github.com/getsentry/XcodeBuildMCP",
      description_source: "package",
    },
    blender: {
      description: "Live Blender 3D viewport and scene bridge supporting procedural geometry generation, mesh inspection, and bpy Python script execution.",
      github_url: "https://github.com/ahujasid/blender-mcp",
      description_source: "builtin",
    },
    playwright: {
      description: "Headless browser automation framework providing multi-browser navigation, DOM interaction, network tracing, and visual screenshot capture.",
      github_url: "https://github.com/microsoft/playwright-mcp",
      description_source: "package",
    },
    headroom: {
      description: "Compresses agent context, retrieves original content by reference, and reports compression statistics.",
      github_url: "https://github.com/chopratejas/headroom",
      description_source: "package",
    },
    "apple-docs": {
      description: "Apple Developer Documentation and WWDC archive search providing API reference, sample code, and platform compatibility guides.",
      github_url: "https://github.com/kimsungwhee/apple-docs-mcp",
      description_source: "package",
    },
    node_repl: {
      description: "Node.js REPL interactive JavaScript/TypeScript evaluation environment.",
      github_url: "",
      description_source: "builtin",
    },
    "computer-use": {
      description: "Desktop interaction service providing OS mouse, keyboard, and screen automation.",
      github_url: "",
      description_source: "builtin",
    },
  };

  // MCPManager acquisition and rebinding
  let mcpManager = null;
  async function bindMcpManager() {
    try {
      const mcpPkg = await import("@oh-my-pi/pi-coding-agent/mcp");
      if (mcpPkg?.MCPManager?.instance) {
        const inst = mcpPkg.MCPManager.instance();
        if (inst && inst !== mcpManager) {
          mcpManager = inst;
          subscribeMcpManager();
        }
      }
    } catch {}
  }

  function subscribeMcpManager() {
    if (!mcpManager) return;
    try {
      if (typeof mcpManager.addConnectionStatusListener === "function") {
        const unsub = mcpManager.addConnectionStatusListener(() => {
          if (latestCtx) void collectAndEmitInventory(latestCtx);
        });
        if (typeof unsub === "function") unsubscribers.push(unsub);
      }
      if (typeof mcpManager.addToolsChangedListener === "function") {
        const unsub = mcpManager.addToolsChangedListener(() => {
          if (latestCtx) void collectAndEmitInventory(latestCtx);
        });
        if (typeof unsub === "function") unsubscribers.push(unsub);
      }
    } catch {}
  }

  await bindMcpManager();

  let rtkCheckPromise = null;
  function checkRtkOnce() {
    if (rtkAvailable !== null) return Promise.resolve(rtkAvailable);
    if (!rtkCheckPromise) {
      rtkCheckPromise = (async () => {
        try {
          const res = await pi.exec("rtk", ["--version"], { timeout: 1500 });
          rtkAvailable = res.code === 0;
        } catch {
          rtkAvailable = false;
        }
        return rtkAvailable;
      })();
    }
    return rtkCheckPromise;
  }

  async function collectAndEmitInventory(ctx) {
    activeMemoryId = null;
    if (ctx) latestCtx = ctx;
    const activeContext = ctx || latestCtx;
    const cwd = activeContext?.cwd || process.cwd();
    const ownerSessionId = sessionId;
    token = resolveToken(cwd);
    endpoint = resolveEndpoint(cwd);

    const isRtk = await checkRtkOnce();

    // 1. Native / builtin tools from session (strictly distinguished from MCP)
    let allTools = [];
    try {
      if (typeof activeContext?.getAllTools === "function") {
        allTools = activeContext.getAllTools() || [];
      } else if (Array.isArray(activeContext?.tools)) {
        allTools = activeContext.tools;
      } else {
        allTools = pi.getAllTools() || [];
      }
    } catch {
      allTools = [];
    }

    let activeTools;
    try { if (typeof pi.getActiveTools === "function") activeTools = new Set(pi.getActiveTools()); } catch {}
    const nativeTools = [];
    for (const tool of allTools) {
      const source = tool.sourceInfo?.source;
      if (source === "builtin" || source === "sdk" || source === "extension") {
        const desc = tool.description || NATIVE_TOOL_DESCRIPTIONS[tool.name] || (typeof tool.name === "string" ? `Native tool '${tool.name}'` : "");
        nativeTools.push({
          name: tool.name,
          description: desc,
          source,
          availability: activeTools ? (activeTools.has(tool.name) ? "available" : "unavailable") : "configured",
        });
      }
    }

    // 2. Skills from live AgentRegistry session, active context, or slash commands
    const skills = [];
    const seenSkills = new Set();

    const directSkills = activeContext?.skills || activeContext?.session?.skills || [];
    if (Array.isArray(directSkills)) {
      for (const s of directSkills) {
        if (s?.name && !seenSkills.has(s.name)) {
          seenSkills.add(s.name);
          skills.push({
            name: s.name,
            path: s.filePath || s.path || "",
            description: s.description || `Agent skill '${s.name}'`,
            availability: "provided",
            source: s.source || "omp-runtime",
          });
        }
      }
    }

    try {
      const AgentRegistry = pi.pi?.AgentRegistry || pi.AgentRegistry;
      const agentId = activeContext?.agent?.id;
      if (AgentRegistry && agentId) {
        const liveSession = AgentRegistry.global?.()?.get(agentId)?.session;
        if (liveSession?.sessionManager?.getSessionId?.() === activeContext?.sessionManager?.getSessionId?.()) {
          const liveSkills = liveSession.skills || [];
          for (const s of liveSkills) {
            if (s?.name && !seenSkills.has(s.name)) {
              seenSkills.add(s.name);
              skills.push({
                name: s.name,
                path: s.filePath || s.path || "",
                description: s.description || `Agent skill '${s.name}'`,
                availability: "provided",
                source: s.source || "omp-runtime",
              });
            }
          }
        }
      }
    } catch {}

    if (skills.length === 0) {
      let allCommands = [];
      try {
        allCommands = pi.getCommands() || [];
      } catch {
        allCommands = [];
      }
      for (const cmd of allCommands) {
        if (cmd.source === "skill" || (typeof cmd.name === "string" && cmd.name.startsWith("skill:"))) {
          const skillName = cmd.name.replace(/^skill:/, "");
          if (!seenSkills.has(skillName)) {
            seenSkills.add(skillName);
            skills.push({
              name: skillName,
              path: cmd.path || "",
              description: cmd.description || `Agent skill '${skillName}'`,
              availability: "provided",
              source: "omp-runtime",
            });
          }
        }
      }
    }

    // 3. MCP servers: acquired ONLY from MCPManager when in main agent and matching session
    const isMainAgent = !activeContext?.agent?.kind || activeContext.agent.kind === "main";
    let isSameSession = true;
    try {
      const AgentRegistry = pi.pi?.AgentRegistry;
      const agentId = activeContext?.agent?.id;
      if (AgentRegistry && agentId && activeContext?.sessionManager?.getSessionId) {
        const liveSession = AgentRegistry.global?.()?.get(agentId)?.session;
        isSameSession = liveSession?.sessionManager?.getSessionId?.() === activeContext.sessionManager.getSessionId();
      }
    } catch {}

    const mcpServers = [];
    if (mcpManager && isMainAgent && isSameSession) {
      try {
        const serverNames = mcpManager.getAllServerNames() || [];
        const managerTools = mcpManager.getTools() || [];
        const serverToolsMap = new Map();
        const serverToolDetailsMap = new Map();
        for (const t of managerTools) {
          const sName = t.mcpServerName;
          if (sName) {
            if (!serverToolsMap.has(sName)) serverToolsMap.set(sName, []);
            serverToolsMap.get(sName).push(t.name);
            if (!serverToolDetailsMap.has(sName)) serverToolDetailsMap.set(sName, []);
            serverToolDetailsMap.get(sName).push({
              name: t.name,
              description: typeof t.description === "string" ? t.description : "",
            });
          }
        }

        for (const sName of serverNames) {
          const status = mcpManager.getConnectionStatus(sName) || "configured";
          const toolNames = serverToolsMap.get(sName) || [];
          const tools = serverToolDetailsMap.get(sName) || [];
          const key = sName.toLowerCase();
          const known = KNOWN_MCP_SERVERS[key] || KNOWN_MCP_SERVERS[sName];
          let desc = known?.description;
          if (!desc) {
            if (toolNames.length > 0) {
              desc = `Configured Model Context Protocol server exposing ${toolNames.length} tool${toolNames.length === 1 ? "" : "s"}: ${toolNames.slice(0, 5).join(", ")}${toolNames.length > 5 ? "..." : ""}.`;
            } else {
              desc = `Configured Model Context Protocol server providing external tools and resources.`;
            }
          }
          const entry = {
            name: sName,
            connection: status,
            availability: status === "connected" ? "available" : "configured",
            tool_names: toolNames,
            tools: tools,
            source: "omp-runtime",
            description: desc,
            description_source: known?.description_source || (tools.length > 0 ? "connected-metadata" : "omp-runtime"),
          };
          if (known?.github_url) {
            entry.github_url = known.github_url;
            entry.source_url = known.github_url;
          }
          mcpServers.push(entry);
        }
      } catch {}
    }

    let commands = referenceCommands();
    try {
      const live = (pi.getCommands() || []).filter(item => item.source !== "skill").map(item => ({
        name: "/" + item.name, description: String(item.description || "").slice(0, 512),
        source: "omp-runtime", availability: "provided",
      }));
      commands = [...new Map([...commands, ...live].map(item => [item.name, item])).values()];
    } catch {}
    const memorySystems = await memoryInventory(pi, activeContext);
    if (ownerSessionId !== sessionId) return;
    activeMemoryId = memorySystems?.find(system => system.enabled && ["omp-hindsight", "omp-local", "omp-mnemopi", "omp-sharpshooter"].includes(system.id))?.id || null;

    const inventoryEvent = {
      type: "RUNTIME_INVENTORY",
      session_id: sessionId,
      run_id: currentRunId || sessionId,
      cwd,
      inventory: {
        source: "omp-runtime",
        mcp: mcpServers,
        skills,
        integrations: [
          {
            name: "rtk",
            type: "cli-wrapper",
            availability: isRtk ? "available" : "unavailable",
            source: "system-path",
            description: "Token-optimized CLI proxy for shell commands with output filtering and token savings analytics",
            source_url: "https://github.com/rtk-ai/rtk",
            github_url: "https://github.com/rtk-ai/rtk",
            description_source: "cli-integration",
          },
        ],
        native_tools: nativeTools,
        commands,
        ...(memorySystems ? {memory_systems: memorySystems} : {}),
      },
    };

    enqueueEvent(inventoryEvent);
  }

  // Session lifecycle
  pi.on("session_start", async (_event, ctx) => {
    latestCtx = ctx;
    try {
      if (ctx?.sessionManager?.getSessionId) {
        sessionId = ctx.sessionManager.getSessionId() || sessionId;
      }
    } catch {}
    activeCalls.clear();
    currentRunId = null;
    observeContext(ctx);
    await bindMcpManager();
    await collectAndEmitInventory(ctx);
  });

  pi.on("session_switch", async (_event, ctx) => {
    latestCtx = ctx;
    try {
      if (ctx?.sessionManager?.getSessionId) {
        sessionId = ctx.sessionManager.getSessionId() || sessionId;
      }
    } catch {}
    activeCalls.clear();
    currentRunId = null;
    observeContext(ctx);
    await bindMcpManager();
    await collectAndEmitInventory(ctx);
  });

  pi.on("session_branch", async (_event, ctx) => {
    latestCtx = ctx;
    try {
      if (ctx?.sessionManager?.getSessionId) {
        sessionId = ctx.sessionManager.getSessionId() || sessionId;
      }
    } catch {}
    activeCalls.clear();
    currentRunId = null;
    observeContext(ctx);
    await bindMcpManager();
    await collectAndEmitInventory(ctx);
  });

  pi.on("session_shutdown", () => {
    if (observationTimer != null) timerContext?.clearTimer?.(observationTimer);
    observationTimer = null; observationOwner = null; observationSignature = "";
    activeCalls.clear();
    while (unsubscribers.length > 0) {
      try {
        const unsub = unsubscribers.pop();
        if (typeof unsub === "function") unsub();
      } catch {}
    }
  });

  // Agent turn lifecycle
  pi.on("before_agent_start", (event, ctx) => {
    if (event?.reason === "revive") return;
    latestCtx = ctx;
    const prevSession = sessionId;
    try {
      if (ctx?.sessionManager?.getSessionId) {
        sessionId = ctx.sessionManager.getSessionId() || sessionId;
      }
    } catch {}
    if (sessionId !== prevSession) {
      activeCalls.clear();
      void collectAndEmitInventory(ctx);
    } else {
      void collectAndEmitInventory(ctx);
    }
    currentRunId = `omp:${sessionId}:${crypto.randomUUID()}`;
    const promptText = typeof event?.prompt === "string" ? event.prompt : "";
    enqueueEvent({
      type: "PROMPT_START",
      run_id: currentRunId,
      session_id: sessionId,
      prompt: promptText,
      source: "omp",
      cwd: ctx?.cwd || process.cwd(),
    });
  });

  pi.on("agent_end", (event, ctx) => {
    latestCtx = ctx;
    if (currentRunId) {
      let isError = Boolean(event?.isError || event?.error || event?.aborted);
      if (Array.isArray(event?.messages) && event.messages.length > 0) {
        const lastMsg = event.messages[event.messages.length - 1];
        if (lastMsg?.stopReason === "error" || lastMsg?.stopReason === "aborted" || lastMsg?.isError) {
          isError = true;
        }
      }
      if (event?.outcome && (event.outcome === "error" || event.outcome === "aborted" || event.outcome === "failed")) {
        isError = true;
      }
      const response = [...(event?.messages || [])].reverse().find(message => message?.role === "assistant");
      const publishedText = typeof response?.content === "string" ? response.content : Array.isArray(response?.content) ? response.content.filter(part => part?.type === "text" && typeof part.text === "string").map(part => part.text).join("\n") : "";
      enqueueEvent({
        type: "GENERATION_END",
        run_id: currentRunId,
        session_id: sessionId,
        status: isError ? "failed" : "completed",
        success: !isError,
        result: publishedText,
        cwd: ctx?.cwd || process.cwd(),
      });
      currentRunId = null;
    }
  });

  // Tool execution observability: fires after middleware rewrites (including RTK rewrite)
  pi.on("tool_execution_start", (event) => {
    if (!event) return;
    const { toolCallId, toolName, args } = event;
    const startTime = Date.now();
    activeCalls.set(toolCallId, { toolName, args, startTime });

    const payload = {
      type: "TOOL_CALL_START",
      run_id: currentRunId || sessionId,
      session_id: sessionId,
      tool_name: toolName,
      call_id: toolCallId,
      input: args,
      args,
      timestamp: startTime,
      cwd: latestCtx?.cwd || process.cwd(),
      file_snapshots: captureFileSnapshots(toolName, args, latestCtx?.cwd, "before"),
    };

    if (typeof args?.command === "string") {
      payload.command = args.command;
      if (/^\s*rtk\s+/.test(args.command)) {
        payload.integration = "rtk";
      }
    }
    if (typeof args?.path === "string") {
      payload.path = args.path;
      if (args.path.startsWith("skill://")) {
        payload.skill_name = args.path.replace("skill://", "").split("/")[0];
      }
    }

    enqueueEvent(payload);
  });

  pi.on("tool_execution_end", (event) => {
    if (!event) return;
    const { toolCallId, toolName, result, isError } = event;
    const startData = activeCalls.get(toolCallId);
    if (!startData) return;
    activeCalls.delete(toolCallId);

    const durationMs = startData ? Date.now() - startData.startTime : 0;
    const args = startData?.args;

    const payload = {
      type: "TOOL_CALL_END",
      run_id: currentRunId || sessionId,
      session_id: sessionId,
      tool_name: toolName,
      call_id: toolCallId,
      input: args,
      args,
      result,
      output: typeof result === "string" ? result : JSON.stringify(result ?? ""),
      success: !isError,
      duration_ms: durationMs,
      cwd: latestCtx?.cwd || process.cwd(),
      timestamp: Date.now(),
      file_snapshots: captureFileSnapshots(toolName, args, latestCtx?.cwd, "after"),
    };

    // Check result.details for exact MCP ownership (present on direct MCP & write xd dispatch)
    const details = result?.details;
    if (details && typeof details === "object") {
      const server = details.serverName || details.mcpServerName;
      const mcpTool = details.mcpToolName || details.toolName;
      if (typeof server === "string" && server.trim()) payload.mcp_server = server.trim();
      if (typeof mcpTool === "string" && mcpTool.trim()) payload.mcp_tool = mcpTool.trim();
    }

    if (typeof args?.command === "string") {
      payload.command = args.command;
      if (/^\s*rtk\s+/.test(args.command)) {
        payload.integration = "rtk";
      }
    }
    if (typeof args?.path === "string") {
      payload.path = args.path;
      if (args.path.startsWith("skill://")) {
        payload.skill_name = args.path.replace("skill://", "").split("/")[0];
      }
    }

    enqueueEvent(payload);
    emitObservation(latestCtx);
  });
  for (const name of ["auto_compaction_start", "auto_compaction_end", "session_compact"]) {
    pi.on(name, (event, ctx) => emitObservation(ctx, {compaction: {
      status: name.endsWith("start") ? "running" : "completed",
      method: name.startsWith("auto_") ? "automatic" : "reported",
      tokens_before: event?.tokensBefore, tokens_after: event?.tokensAfter,
      at: new Date().toISOString(),
    }}));
  }
  for (const name of ["auto_retry_start", "auto_retry_end"]) {
    pi.on(name, (event, ctx) => emitObservation(ctx, {retry: {
      status: name.endsWith("start") ? "retrying" : "finished", attempt: event?.attempt,
      delay_ms: event?.delayMs, reason: typeof event?.error === "string" ? event.error.slice(0, 500) : undefined,
    }}));
  }
  for (const name of ["tool_approval_requested", "tool_approval_resolved", "plan_review_requested", "plan_review_resolved"]) {
    pi.on(name, (event, ctx) => emitObservation(ctx, {approval: {
      status: name.endsWith("requested") ? "pending" : "resolved",
      kind: name.startsWith("plan_") ? "plan" : "tool", title: event?.title || event?.toolName,
    }}));
  }
  pi.on("mode_changed", (_event, ctx) => emitObservation(ctx));
}
