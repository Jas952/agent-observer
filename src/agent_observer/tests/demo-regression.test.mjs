import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, basename } from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { runInNewContext } from 'node:vm';

// Real dashboard/assets, isolated catalog/storage, no production server or history.
// Run: node --test src/agent_observer/tests/demo-regression.test.mjs
const web = new URL('../web/', import.meta.url);
const documentNodes = [
  // 4 foundational guides
  { id: 'document_0', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/guide.md', path: 'docs/standards/guide.md', description: 'Standards overview guide' },
  { id: 'document_1', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/guide.md', path: 'docs/geometry/guide.md', description: 'Geometry modeling guide' },
  { id: 'document_2', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/guide.md', path: 'docs/validation/guide.md', description: 'Validation rules guide' },
  { id: 'document_3', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/guide.md', path: 'docs/reference/guide.md', description: 'Reference catalogue guide' },

  // Standards nested folders & non-.md formats
  { id: 'document_4', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/iso/iso-286-tolerances.pdf', path: 'docs/standards/iso/iso-286-tolerances.pdf', description: 'ISO 286 limit fits specification' },
  { id: 'document_5', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/iso/iso-965-threads.json', path: 'docs/standards/iso/iso-965-threads.json', description: 'ISO 965 metric thread pitch data' },
  { id: 'document_6', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/iso/fits-table.csv', path: 'docs/standards/iso/fits-table.csv', description: 'Standard clearance fits matrix' },
  { id: 'document_7', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/din/din-76-undercuts.txt', path: 'docs/standards/din/din-76-undercuts.txt', description: 'DIN 76 thread undercut dimensions' },
  { id: 'document_8', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/din/din-6885-keys.step', path: 'docs/standards/din/din-6885-keys.step', description: 'DIN 6885 drive key CAD solids' },
  { id: 'document_9', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/gost/gost-21474-knurling.step', path: 'docs/standards/gost/gost-21474-knurling.step', description: 'GOST 21474 diamond knurl solid' },
  { id: 'document_10', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/gost/gost-10549-thread-runout.pdf', path: 'docs/standards/gost/gost-10549-thread-runout.pdf', description: 'GOST 10549 thread runout limits' },
  { id: 'document_11', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/tolerances/linear-tolerances.json', path: 'docs/standards/tolerances/linear-tolerances.json', description: 'General linear tolerance classes' },
  { id: 'document_12', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/tolerances/angular-tolerances.txt', path: 'docs/standards/tolerances/angular-tolerances.txt', description: 'Angular tolerance standards' },
  { id: 'document_13', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/tolerances/surface-roughness.csv', path: 'docs/standards/tolerances/surface-roughness.csv', description: 'Surface roughness Ra conversions' },
  { id: 'document_14', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/materials/steel-properties.json', path: 'docs/standards/materials/steel-properties.json', description: 'AISI 1045 steel thermal and yield constants' },
  { id: 'document_15', kind: 'document', group: 'Knowledge', category: 'standards', label: 'docs/standards/materials/aluminum-alloys.txt', path: 'docs/standards/materials/aluminum-alloys.txt', description: 'Al 6061-T6 mechanical properties' },

  // Geometry nested folders & non-.md formats
  { id: 'document_16', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/cad/housing-flange.step', path: 'docs/geometry/cad/housing-flange.step', description: 'Flanged bearing housing solid STEP' },
  { id: 'document_17', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/cad/shaft-coupler.scad', path: 'docs/geometry/cad/shaft-coupler.scad', description: 'Parametric flexible shaft coupler OpenSCAD source' },
  { id: 'document_18', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/cad/spindle-assembly.step', path: 'docs/geometry/cad/spindle-assembly.step', description: 'High-speed spindle solid assembly' },
  { id: 'document_19', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/cad/bearing-mount.scad', path: 'docs/geometry/cad/bearing-mount.scad', description: '608 bearing press-fit mount module' },
  { id: 'document_20', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/mesh/bearing-bracket.stl', path: 'docs/geometry/mesh/bearing-bracket.stl', description: 'Triangulated bracket mesh for rapid print' },
  { id: 'document_21', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/mesh/spacer-bushing.stl', path: 'docs/geometry/mesh/spacer-bushing.stl', description: 'Precision axle spacer STL' },
  { id: 'document_22', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/mesh/rotor-cap.stl', path: 'docs/geometry/mesh/rotor-cap.stl', description: 'Protective rotor dust cap STL' },
  { id: 'document_23', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/mesh/pulley-wheel.stl', path: 'docs/geometry/mesh/pulley-wheel.stl', description: 'GT2 timing pulley mesh model' },
  { id: 'document_24', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/curves/cam-profile.dxf', path: 'docs/geometry/curves/cam-profile.dxf', description: 'Harmonic displacement cam profile curve' },
  { id: 'document_25', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/curves/involute-gear.json', path: 'docs/geometry/curves/involute-gear.json', description: 'Involute tooth profile coordinate series' },
  { id: 'document_26', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/features/counterbore-specs.txt', path: 'docs/geometry/features/counterbore-specs.txt', description: 'Counterbore and countersink tooling depths' },
  { id: 'document_27', kind: 'document', group: 'Knowledge', category: 'geometry', label: 'docs/geometry/features/chamfer-rules.step', path: 'docs/geometry/features/chamfer-rules.step', description: 'Reference edge breaks and corner fillets' },

  // Validation nested folders & non-.md formats
  { id: 'document_28', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/rules/clearance-limits.json', path: 'docs/validation/rules/clearance-limits.json', description: 'CNC tooling clearance thresholds' },
  { id: 'document_29', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/rules/stress-analysis.csv', path: 'docs/validation/rules/stress-analysis.csv', description: 'Von Mises yield stress limits' },
  { id: 'document_30', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/rules/minimum-wall.txt', path: 'docs/validation/rules/minimum-wall.txt', description: 'Additive and subtractive wall constraints' },
  { id: 'document_31', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/rules/interference-checks.json', path: 'docs/validation/rules/interference-checks.json', description: 'Static assembly interference rules' },
  { id: 'document_32', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/reports/thermal-test.pdf', path: 'docs/validation/reports/thermal-test.pdf', description: 'Steady-state heat dissipation simulation' },
  { id: 'document_33', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/reports/vibration-log.csv', path: 'docs/validation/reports/vibration-log.csv', description: 'Resonance modal response test logs' },
  { id: 'document_34', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/reports/pressure-drop.txt', path: 'docs/validation/reports/pressure-drop.txt', description: 'Coolant manifold pressure drop log' },
  { id: 'document_35', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/benchmarks/fe-mesh-report.pdf', path: 'docs/validation/benchmarks/fe-mesh-report.pdf', description: 'Tetrahedral mesh convergence metrics' },
  { id: 'document_36', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/benchmarks/solver-convergence.json', path: 'docs/validation/benchmarks/solver-convergence.json', description: 'Nonlinear Newton-Raphson iteration log' },
  { id: 'document_37', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/checks/manifold-check.txt', path: 'docs/validation/checks/manifold-check.txt', description: 'Watertight boundary topology check' },
  { id: 'document_38', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/checks/draft-angles.step', path: 'docs/validation/checks/draft-angles.step', description: 'Draft angle analysis surface' },
  { id: 'document_39', kind: 'document', group: 'Knowledge', category: 'validation', label: 'docs/validation/checks/tool-reach.stl', path: 'docs/validation/checks/tool-reach.stl', description: '5-axis tool envelope reach boundary' },

  // Reference nested folders & non-.md formats
  { id: 'document_40', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/hardware/m4-hex-bolt.step', path: 'docs/reference/hardware/m4-hex-bolt.step', description: 'DIN 912 M4x16 socket head screw CAD' },
  { id: 'document_41', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/hardware/608zz-bearing.stl', path: 'docs/reference/hardware/608zz-bearing.stl', description: 'Standard 608-ZZ shielded bearing mesh' },
  { id: 'document_42', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/hardware/retaining-ring.step', path: 'docs/reference/hardware/retaining-ring.step', description: 'Shaft snap ring solid geometry' },
  { id: 'document_43', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/hardware/compression-spring.step', path: 'docs/reference/hardware/compression-spring.step', description: 'Ground-end helical spring STEP' },
  { id: 'document_44', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/manuals/operator-guide.txt', path: 'docs/reference/manuals/operator-guide.txt', description: 'Machinist CNC setup checklist' },
  { id: 'document_45', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/manuals/cnc-cheatsheet.pdf', path: 'docs/reference/manuals/cnc-cheatsheet.pdf', description: 'G-code and feed rate reference' },
  { id: 'document_46', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/manuals/openscad-cheatsheet.txt', path: 'docs/reference/manuals/openscad-cheatsheet.txt', description: 'OpenSCAD syntax quick reference' },
  { id: 'document_47', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/diagrams/assembly-schematic.png', path: 'docs/reference/diagrams/assembly-schematic.png', description: 'Complete spindle sub-assembly schematic' },
  { id: 'document_48', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/diagrams/exploded-view.png', path: 'docs/reference/diagrams/exploded-view.png', description: 'Exploded BOM diagram with callouts' },
  { id: 'document_49', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/tables/drill-tap-sizes.csv', path: 'docs/reference/tables/drill-tap-sizes.csv', description: 'Metric and imperial tap drill size table' },
  { id: 'document_50', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/tables/thread-pitch.json', path: 'docs/reference/tables/thread-pitch.json', description: 'Standard metric pitch and lead values' },
  { id: 'document_51', kind: 'document', group: 'Knowledge', category: 'reference', label: 'docs/reference/notes/release-notes.txt', path: 'docs/reference/notes/release-notes.txt', description: 'CAD knowledge base revision notes' },
];

const document_links = [
  { from: 'document_0', to: 'document_4' },
  { from: 'document_4', to: 'document_5' },
  { from: 'document_5', to: 'document_6' },
  { from: 'document_1', to: 'document_16' },
  { from: 'document_16', to: 'document_20' },
  { from: 'document_2', to: 'document_28' },
  { from: 'document_28', to: 'document_32' },
  { from: 'document_3', to: 'document_40' },
  { from: 'document_40', to: 'document_41' },
];

const nodes = [
  ...documentNodes,
  ...['OpenSCAD CLI', 'SQLite Local Database', 'BOSL2 Package Manager'].map((label, i) => ({
    id: `tool_${i}`, kind: 'tool', category: 'tool', label, description: 'Fixture service', match: {},
  })),
  { id: 'mcp_demo', kind: 'mcp', group: 'capabilities', name: 'demo', label: 'Demo MCP', category: 'MCP', tool_names: ['mcp__demo__inspect'] },
  { id: 'skill_demo', kind: 'skill', group: 'capabilities', name: 'demo', label: 'Demo skill', category: 'Skills', path: 'skill://demo' },
  { id: 'integration_rtk', kind: 'integration', group: 'capabilities', name: 'rtk', label: 'RTK', category: 'Integration' },
  { id: 'memory_demo', kind: 'memory', group: 'memory', label: 'Hindsight', memory_system_id: 'demo', provider: 'hindsight', endpoint: 'http://127.0.0.1:1', bank_ids: ['demo'] },
].map(node => ({ ...node, status: 'idle', reason: '', payload: '' }));
const config = { project_title: 'Regression fixture', project_root: '/observer-demo-regression', agent_label: 'Fixture agent', agent_spec: [], nodes, document_links, memory_systems: [] };
const bootstrap = { config, state: { nodes: {}, edges: [], orchestrator_phase: 'idle' }, runs: [], history: [] };
const alternateRoot = '/observer-other-project';
const sharedDocument = nodes.find(node => node.kind === 'document');
const alternateDocuments = [
  {...sharedDocument, absolute_path: `${alternateRoot}/${sharedDocument.path}`},
  {id: 'other_project_document', kind: 'document', label: 'docs/other-project.md', path: 'docs/other-project.md',
    absolute_path: `${alternateRoot}/docs/other-project.md`, description: 'Other project reference'}
];
const alternateBootstrap = {...bootstrap, config: {...config, project_title: 'Other project',
  project_root: alternateRoot, nodes: alternateDocuments, document_links: []}};
function connectionFixture(project, current) {
  const projects = [config, alternateBootstrap.config];
  const workspaces = projects.map(item => ({path: item.project_root, name: item.project_title,
    current: item.project_root === current, installed: true, module: 'codex', enabled: false, revision: 'connection-fixture'}));
  return {current_project: current, workspaces, workspace: workspaces.find(item => item.path === project),
    modules: [], resources: {mcp: 0, skills: 0, documents: ['docs']}};
}
const requests = [];
let server, chrome, profile, base, cdp, nextId = 0;
let localServices = [], localProcesses = [], memorySystems = [];
let failFolderAnalysis = false, failGraphGeneration = false;
let visibilityPreferences = null;
let sessionFixtures = null;
function sessionFixtureDetail(root,id) {
  const row=sessionFixtures?.get(root)?.find(row=>row.session_id===id);
  if(!row)return null;
  return {config:row.config||(root===alternateRoot?alternateBootstrap.config:config),history:[],
    state:{run_id:'fixture-'+id,prompt:row.prompt||'',nodes:{},edges:[],orchestrator_phase:'idle',context:{session_id:id,application:row.application||'Codex'}}};
}
function visibilityConfig(selected) {
  return {...selected,...visibilityPreferences?.get(selected.project_root)};
}
async function visibilityResponse(req,res,root,selected) {
  let preferences=visibilityPreferences.get(root);
  if(req.method==='POST'){
    let raw='';for await(const chunk of req)raw+=chunk;
    const body=JSON.parse(raw);
    if(body.revision!==preferences.revision){res.writeHead(409).end('{"error":"Configuration changed"}');return}
    preferences={...preferences,...body,revision:String(Number(preferences.revision)+1)};
    visibilityPreferences.set(root,preferences);
  }
  res.setHeader('Content-Type','application/json');
  res.end(JSON.stringify({...preferences,catalog:visibilityConfig(selected)}));
}
const pending = new Map();
const exceptions = [];

async function command(method, params = {}) {
  const id = ++nextId;
  const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  cdp.send(JSON.stringify({ id, method, params }));
  return result;
}
async function evaluate(expression) {
  const result = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  assert.ok(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function preparePage(url = base) {
  await command('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await command('Page.navigate', { url });
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate("typeof initialized !== 'undefined' && initialized && Boolean(document.querySelector('#memory_demo')) && Boolean(documentFilesView) && Boolean(documentBubbleView) && Boolean(document.querySelector('#language-toggle'))")) break;
    await pause(50);
  }
}

before(async () => {
  server = createServer(async (req, res) => {
    requests.push({ method: req.method, url: req.url });
    const pathname = req.url.split('?')[0];
    if (pathname === '/' || pathname === '/other-project/') {
      res.setHeader('Content-Type', 'text/html');
      let html = readFileSync(new URL('dashboard.html', web), 'utf8');
      if (!html.includes('installation.js')) {
        html = html.replace('</head>', `<link rel="stylesheet" href="/assets/installation.css"><script src="/assets/installation.js"></script></head>`);
      }
      const isDemo = new URL(req.url, 'http://fixture').searchParams.has('demo');
      const selected = isDemo ? {...bootstrap,config:{...config,project_root:'/__observer_demo__',project_title:'DEMO fixture'}} : pathname === '/other-project/' ? alternateBootstrap : bootstrap;
      const initial={...selected,config:visibilityConfig(selected.config)};
      const requested=new URL(req.url,'http://fixture').searchParams.get('session'),detail=sessionFixtureDetail(selected.config.project_root,requested);
      if(detail)Object.assign(initial,detail);
      res.end(html.replace('</head>', `<script>window.__BOOTSTRAP__=${JSON.stringify(initial)}</script></head>`));
    } else if (req.url.startsWith('/assets/')) {
      const name = basename(req.url);
      if (!/^[a-z0-9-]+(?:\.ru)?\.(?:js|css)$/.test(name)) { res.writeHead(404).end(); return; }
      res.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : 'text/css');
      res.end(readFileSync(new URL(name, web)));
    } else if (pathname === '/api/project-settings' || pathname === '/api/graph-settings') {
      const query = new URL(req.url, 'http://fixture').searchParams;
      const root = query.get('project') || config.project_root;
      const endpoint = query.get('endpoint') || '/api/graph-settings';
      const foreign = root === alternateRoot, selected = visibilityConfig(foreign ? alternateBootstrap.config : config);
      res.setHeader('Content-Type', 'application/json');
      if (endpoint === '/api/graph-settings') res.end(JSON.stringify({project_root:root,config:selected,local_services:[],
        rows:[{id:foreign?'mcp_foreign':'mcp_current',kind:'mcp',name:foreign?'Foreign MCP':'Current MCP',detected:true,node_id:'mcp_demo'}]}));
      else if (endpoint === '/api/block-settings' && visibilityPreferences) await visibilityResponse(req,res,root,selected);
      else if (endpoint === '/api/local-services') res.end(JSON.stringify({project_root:root,services:localServices.map(service=>({...service,name:'Foreign service'})),processes:localProcesses.map(process=>({...process,name:'Foreign worker',cwd:root}))}));
      else if (endpoint.startsWith('/api/local-services/browser-fixture/logs')) res.end(JSON.stringify({lines:[{seq:1,stream:'stdout',text:'FOREIGN-PROJECT-OUTPUT'}],next_cursor:1,truncated:false}));
      else if (endpoint === '/api/connections/status') res.end(JSON.stringify(connectionFixture(root, config.project_root)));
      else if (endpoint === '/api/sessions') res.end(JSON.stringify({sessions:[{session_id:'foreign-session',project_path:root,status:'idle'}]}));
      else res.writeHead(404).end();
    } else if (req.url.startsWith('/api/connections') || req.url.startsWith('/api/installations/')) {
      const current = (req.headers.referer || '').includes('/other-project/') ? alternateRoot : config.project_root;
      let text = '';
      for await (const chunk of req) text += chunk;
      const body = text ? JSON.parse(text) : {};
      res.setHeader('Content-Type', 'application/json');
      if (req.url === '/api/connections' || req.url === '/api/connections/status') {
        res.end(JSON.stringify(connectionFixture(current, current)));
      } else if (req.url === '/api/connections/inspect') {
        res.end(JSON.stringify(connectionFixture(body.project, current)));
      } else if (req.url === '/api/connections/workspaces') {
        res.end(JSON.stringify(connectionFixture(body.project, current).workspace));
      } else if (req.url === '/api/connections/choose-folder') {
        res.end(JSON.stringify({path: current === alternateRoot ? config.project_root : alternateRoot}));
      } else if (req.url === '/api/connections/analyze') {
        if (failFolderAnalysis) {res.writeHead(503).end(JSON.stringify({detail:'Session index cannot be read'}));return;}
        const ready = body.repository_mode === 'working_repository';
        res.end(JSON.stringify({workspace:connectionFixture(body.project,current).workspace,repository_mode:body.repository_mode,
          module:ready?(body.module||'codex'):'project',analysis_id:'folder-scan-fixture',revision:'scan-fixture',ready,
          checks:[{id:'agent',name:'Agent integration',status:ready?'pass':'error',detail:ready?'Readable project-scoped '+(body.module==='omp'?'OMP':'Codex')+' journals':'Project telemetry integration is missing'},
            {id:'documents',name:'Documents',status:'pass',detail:'Project Markdown references found'},
            {id:'result',name:'Result',status:'waiting',detail:'Available after a captured request'}],
          logs:[{level:'info',message:'Scanned selected folder without starting an agent'},{level:'info',message:'<script>untrusted text</script>'}],
          sessions:[{session_id:'folder-session',session_name:'Folder session',application:'Codex',status:'idle',project_path:body.project}]}));
      } else if (req.url === '/api/connections/generate' && body.analysis_id === 'folder-scan-fixture' && body.revision === 'scan-fixture') {
        if (failGraphGeneration) {res.writeHead(409).end(JSON.stringify({detail:'Folder settings changed; analyze again'}));return;}
        const next=body.project===alternateRoot?alternateBootstrap:bootstrap;
        res.end(JSON.stringify({current:body.project===current,url:body.project===alternateRoot?`${base}/other-project/`:base,
          ...connectionFixture(body.project,current),config:next.config,state:next.state,history:[]}));
      } else { res.writeHead(404).end(); }
    } else if (pathname === '/api/block-settings' && visibilityPreferences) {
      await visibilityResponse(req,res,config.project_root,config);
    } else if (pathname === '/api/project-request' || pathname === '/api/bootstrap') {
      let raw='';for await(const chunk of req)raw+=chunk;
      const body=raw?JSON.parse(raw):{},root=body.project||config.project_root,path=body.path||req.url;
      const query=new URL(path,'http://fixture'),selected=root===alternateRoot?alternateBootstrap:bootstrap;
      const rows=sessionFixtures?.get(root)||[];
      res.setHeader('Content-Type','application/json');
      if(query.pathname==='/api/bootstrap'){
        const detail=sessionFixtureDetail(root,query.searchParams.get('session'));
        res.end(JSON.stringify({...selected,...detail,sessions:rows,default_session_id:rows[0]?.session_id||null}));
      } else if(query.pathname==='/api/sessions')res.end(JSON.stringify({sessions:rows,default_session_id:rows[0]?.session_id||null}));
      else if(query.pathname.startsWith('/api/sessions/')){
        const detail=sessionFixtureDetail(root,decodeURIComponent(query.pathname.slice('/api/sessions/'.length)));
        res.writeHead(detail?200:404).end(JSON.stringify(detail||{detail:'Session unavailable'}));
      } else if(query.pathname==='/api/memory-systems')res.end(JSON.stringify({systems:memorySystems}));
      else if(query.pathname==='/api/local-services')res.end(JSON.stringify({project_root:root,services:[]}));
      else res.writeHead(404).end(JSON.stringify({detail:'Unknown scoped fixture endpoint'}));
    } else if (pathname === '/api/session-open' && sessionFixtures) {
      let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);
      const detail=sessionFixtureDetail(body.project,body.session_id);
      if(!detail){res.writeHead(404).end('{"detail":"Session unavailable"}');return}
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify({project:body.project,session_id:body.session_id,url:base+'/other-project/?session='+encodeURIComponent(body.session_id)}));
    } else if (pathname === '/api/session-delete' && sessionFixtures) {
      let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);
      if(body.confirmed!==true){res.writeHead(400).end('{"detail":"Confirmation required"}');return}
      sessionFixtures.set(body.project,(sessionFixtures.get(body.project)||[]).filter(row=>row.session_id!==body.session_id));
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify({deleted:true,...body}));
    } else if (req.url === '/api/session-projects') {
      const current = (req.headers.referer || '').includes('/other-project/') ? alternateRoot : config.project_root;
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({projects:[config,alternateBootstrap.config].map(item=>({path:item.project_root,name:item.project_title,current:item.project_root===current,repository_mode:'working_repository',sessions:sessionFixtures?.get(item.project_root)||[],graph_url:null}))}));
    } else if (req.url === '/api/sessions') {
      const rows=sessionFixtures?.get((req.headers.referer||'').includes('/other-project/')?alternateRoot:config.project_root)||[];
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify({sessions:rows,default_session_id:rows[0]?.session_id||null}));
    } else if (pathname.startsWith('/api/sessions/') && sessionFixtures) {
      const root=(req.headers.referer||'').includes('/other-project/')?alternateRoot:config.project_root;
      const detail=sessionFixtureDetail(root,decodeURIComponent(pathname.slice('/api/sessions/'.length)));
      res.setHeader('Content-Type','application/json');res.writeHead(detail?200:404);res.end(JSON.stringify(detail||{detail:'Session unavailable'}));
    } else if (req.url === '/api/sessions/idle-session') {
      res.setHeader('Content-Type', 'application/json');res.end(JSON.stringify({config,history:[],state:{nodes:{},edges:[],context:{session_id:'idle-session'},orchestrator_phase:'idle'}}));
    } else if (pathname === '/api/memory-systems') {
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({systems:memorySystems}));
    } else if (pathname === '/api/local-services') {
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ services: localServices, processes:localProcesses }));
    } else if (req.url.startsWith('/api/local-services/browser-fixture/logs?')) {
      const after = Number(new URL(req.url, 'http://fixture').searchParams.get('after'));
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ lines: after ? [] : [{ seq: 1, stream: 'stderr', text: '<img src=x onerror=alert(1)> service output' }], next_cursor: 1, truncated: false }));
    } else if (pathname === '/api/local-processes/process-777/stop') {
      let raw='';for await(const chunk of req)raw+=chunk;
      if(JSON.parse(raw).identity!=='worker-identity'){res.writeHead(409).end('{"detail":"Process changed"}');return}
      localProcesses=[];res.setHeader('Content-Type','application/json');res.end('{"status":"stopping"}');
    } else { res.writeHead(404).end(); }
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  base = `http://127.0.0.1:${server.address().port}`;
  profile = mkdtempSync(join(tmpdir(), 'observer-demo-test-'));
  chrome = spawn(process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
    '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  const endpoint = await new Promise((resolve, reject) => {
    let logs = ''; const deadline = setTimeout(() => reject(new Error('Chrome did not start')), 20000);
    chrome.once('error', reject); chrome.once('exit', code => reject(new Error(`Chrome exited ${code}: ${logs}`)));
    chrome.stderr.on('data', chunk => { logs += chunk; const match = logs.match(/DevTools listening on (ws:\/\/[^\s]+)/); if (match) { clearTimeout(deadline); resolve(match[1]); } });
  });
  const target = await (await fetch(endpoint.replace('ws:', 'http:').replace(/\/devtools\/browser\/.*/, '/json/new?about:blank'), { method: 'PUT' })).json();
  cdp = new WebSocket(target.webSocketDebuggerUrl); await once(cdp, 'open');
  cdp.addEventListener('close',()=>{for(const waiter of pending.values())waiter.reject(new Error('Private browser connection closed'));pending.clear()});
  cdp.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
    const waiter = pending.get(message.id); if (!waiter) return;
    pending.delete(message.id); if (message.error) waiter.reject(new Error(JSON.stringify(message.error))); else waiter.resolve(message.result);
  });
  await command('Runtime.enable'); await command('Page.enable');
  await command('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
  await command('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
}, { timeout: 30000 });

after(async () => {
  // Close the actual private browser even if its macOS launcher has already exited.
  if(cdp?.readyState===WebSocket.OPEN)cdp.send(JSON.stringify({id:++nextId,method:'Browser.close'}));
  if (chrome && chrome.exitCode === null && chrome.signalCode === null) {
    const exited = once(chrome, 'exit');
    // The target CDP socket can close without acknowledging Browser.close.
    const forceKill=setTimeout(()=>chrome.kill('SIGKILL'),5000);
    chrome.kill('SIGTERM');
    try { await exited; } finally { clearTimeout(forceKill); }
  }
  cdp?.close();
  server?.closeAllConnections();
  if (server) await new Promise(resolve => server.close(resolve));
  if (profile) rmSync(profile, { recursive: true, force: true });
});

async function runScenario(mode, custom) {
  const assert = (condition, message) => { if (!condition) throw new Error(`${state?.orchestrator_action}: ${message}`); };
  const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  assert(document.querySelector('#connection').textContent === 'Simulation', 'isolated DEMO looks like a pending live connection');
  document.querySelector('#language-toggle').click(); await frame();
  assert(document.documentElement.lang === 'ru' && document.querySelector('#connection').textContent === 'Имитация', 'DEMO connection status is not localized');
  document.querySelector('#language-toggle').click(); await frame();
  assert(document.documentElement.lang === 'en' && document.querySelector('#connection').textContent === 'Simulation', 'DEMO connection status did not return to English');
  if (custom) {
    const rows=[['request','documents','tools'],['agent','commands','capabilities'],['memory','files','result']];
    const size=key=>document.querySelector('.group[data-group="'+key+'"]');
    const widths=rows[0].map((_,column)=>Math.max(...rows.map(row=>size(row[column]).offsetWidth))+200);
    const heights=rows.map(row=>Math.max(...row.map(key=>size(key).offsetHeight))+200);
    for(let row=0;row<rows.length;row++)for(let column=0;column<rows[row].length;column++){
      const key=rows[row][column];
      groupPositions[key]=[50+widths.slice(0,column).reduce((a,b)=>a+b,0),80+heights.slice(0,row).reduce((a,b)=>a+b,0)];
      manuallyPlaced.add(key);
    }
    applyGroupPositions();
  }
  applyDocumentsView(mode); scale = .58; panX = 37; panY = -19; setWorld();
  await frame();
  const layout = () => JSON.stringify({ positions: groupPositions, sizes: groupSizes, mode: documentViewMode, scale, panX, panY });
  const before = layout(), timers = new Map(), nativeTimeout = window.setTimeout;
  let id = -1;
  window.setTimeout = (fn, delay, ...args) => { const token = id--; timers.set(token, { fn, delay, args }); return token; };
  try { startDemo(); } finally { window.setTimeout = nativeTimeout; }
  const scheduled = demoTimers.map(token => timers.get(token)).sort((a, b) => a.delay - b.delay);
  assert(layout() === before, 'starting DEMO changed the arrangement/view/camera');
  const covered = new Set(), geometries = new Set(), previousPaths = new Set(), presented = new Set();
  let maxVisibleAtOnce = 0, stepsChecked = 0;
  const collect = () => {
    const elements = [...svg.querySelectorAll('g[data-edge]')], current = new Set(elements.map(group => group.dataset.edge));
    const bright = elements.filter(group => !group.classList.contains('wire-history') && !group.classList.contains('wire-dimming') && !group.classList.contains('edge-waiting'));
    const context = visibleRouteContext(), boxes = [...document.querySelectorAll('.group')].map(el => ({id: el.dataset.group, ...elementBox(el)})), bounds = JSON.stringify(boxes);
    if (context) presented.add(`${context.stage.index}:${context.phase}`);
    for (const key of previousPaths) if(!current.has(key)){
      const saved=traversedRoutes.get(key),endpoints=saved?[saved.edge.from,saved.edge.to].map(id=>{const el=visibleEndpoint(id);return el?{id,width:el.offsetWidth,...elementBox(el)}:{id,missing:true}}):[];
      assert(false,`traversed path disappeared: ${key}; ${JSON.stringify({blocked:blockedWires,endpoints,owners:boxes.filter(box=>key.split('~').includes(box.id))})}`);
    }
    for (const key of traversedRoutes.keys()) previousPaths.add(key);
    maxVisibleAtOnce = Math.max(maxVisibleAtOnce, bright.length);
    for (const element of elements) {
      const path = element.querySelector('.edge'), length = path.getTotalLength(), geometry = path.getAttribute('d') + bounds;
      assert(length > 0, `empty arrow: ${element.dataset.edge}`);
      const data=wireData.get(element.dataset.edge);
      for(const endpointId of [data.fromId,data.toId]){const endpoint=visibleEndpoint(endpointId),group=endpoint?.closest('.group')?.dataset.group;if(group)covered.add(group)}
      if (!geometries.has(geometry)) {
        geometries.add(geometry);
        for (let at = 0; at <= length; at += 5) {
          const point = path.getPointAtLength(at), hit = boxes.find(box => point.x > box.left + .1 && point.x < box.right - .1 && point.y > box.top + .1 && point.y < box.bottom - .1);
          assert(!hit, `outer arrow enters ${hit?.id}: ${element.dataset.edge}`);
        }
      }
    }
    for (const element of bright) {
      const data = wireData.get(element.dataset.edge);
      const target=visibleEndpoint(data.toId);
      const reached=bright.some(other=>wireData.get(other.dataset.edge)?.toId===data.toId&&!other.querySelector('mask')&&!other.classList.contains('edge-waiting'));
      if(element.querySelector('mask')&&target?.classList.contains('sequence-focus'))assert(reached,`target outlined before any incoming arrow arrived: ${data.toId}`);
      if (context?.kind==='call'&&data.call&&element.dataset.stage===context.key) assert(data.call.ordinal === context.call.ordinal, `unrelated bright call: ${element.dataset.edge}`);
    }
    assert(new Set(bright.map(element=>element.dataset.stage)).size<=1,'Two event stages were highlighted together');
    for(const element of elements){
      const route=routeCache.get(element.dataset.edge);if(!route)continue;
      const owners=element.dataset.edge.split('~').map(id=>boxes.find(box=>box.id===id));
      for(const [index,owner]of owners.entries()){
        const side=index?route.targetSide:route.sourceSide,lane=index?route.spine.at(-1):route.spine[0],channel={left:side==='w'?lane.x:owner.left,right:side==='e'?lane.x:owner.right,top:owner.top,bottom:owner.bottom};
        assert(!wireSegments(route.spine).some(segment=>wireHitsRect(segment.a,segment.b,channel)),`route wraps its own ${index?'target':'source'} marker: ${element.dataset.edge}`);
      }
    }
    const currentRoutes=context?elements.filter(element=>element.dataset.stage===context.key).map(element=>routeCache.get(element.dataset.edge)).filter(Boolean):[];
    for(let i=0;i<currentRoutes.length;i++)for(let j=i+1;j<currentRoutes.length;j++)for(const left of wireSegments(currentRoutes[i].points))for(const right of wireSegments(currentRoutes[j].points)){
      const horizontal=left.a.y===left.b.y;if(horizontal===(right.a.y===right.b.y))continue;
      const point=horizontal?{x:right.a.x,y:left.a.y}:{x:left.a.x,y:right.a.y},inside=(value,a,b)=>value>Math.min(a,b)+.1&&value<Math.max(a,b)-.1;
      const crossing=(horizontal?inside(point.x,left.a.x,left.b.x)&&inside(point.y,right.a.y,right.b.y):inside(point.y,left.a.y,left.b.y)&&inside(point.x,right.a.x,right.b.x));
      const junction=currentRoutes[i].joins.some(a=>currentRoutes[j].joins.some(b=>a.key===b.key&&Math.hypot(a.x-b.x,a.y-b.y)<.1&&Math.hypot(a.x-point.x,a.y-point.y)<.1));
      assert(!crossing||junction,'Active routes cross away from a shared port');
    }
    assert(bright.length <= 8, `cumulative bright mesh detected: ${bright.length} arrows`);
    assert(elements.length <= 28, `repeated calls created individual trails: ${elements.length}`);
    assert(!document.querySelector('.card[data-arrival-pending].sequence-focus'), 'card focused before arrival');
    assert(blockedWires.length === 0, `unrouted connections: ${JSON.stringify(blockedWires)}`);
    assert(layout() === before, 'DEMO changed the arrangement/view/camera');
  };
  async function settlePresentation() {
    for (let attempt = 0; attempt < 160; attempt++) {
      await frame(); collect();
      for (const entry of [...wireEntries.values()]) entry.finish(true);
      await frame(); collect();
      const context = visibleRouteContext();
      if (!routePlayback.queue.length && (!context || routePlayback.finished && context.phase === 'input' && !context.complete)) return;
    }
    throw new Error(`Visual route did not settle: ${visibleRouteContext()?.key}`);
  }
  await settlePresentation();
  for (const task of scheduled) { task.fn(...task.args); await settlePresentation(); stepsChecked++; }
  for (const group of ['request', 'agent', 'documents', 'tools', 'commands', 'files', 'capabilities', 'memory', 'result']) assert(covered.has(group), `no visible connection to ${group}`);
  for (let index = 0; !window.matchMedia('(prefers-reduced-motion: reduce)').matches && index < steps.length; index++) {
    const type = eventType(eventFor(steps[index]));
    if (['PROMPT_START', 'TOOL_CALL_START', 'KNOWLEDGE_ACCESS', 'TOOL_CALL_END', 'GENERATION_END'].includes(type)) assert(presented.has(`${index}:${type === 'TOOL_CALL_END' ? 'output' : 'input'}`), `skipped visual event ${index}: ${type}`);
  }
  const calls = buildCallSequence(steps);
  assert(calls.some(call => call.failed), 'missing failed verification');
  assert(calls.filter(call => call.tool === 'bash').at(-1)?.failed === false, 'missing successful retry');
  for (const kind of ['mcp', 'skill', 'integration', 'memory']) assert(Object.values(state.nodes).some(node => node.kind === kind && node.status === 'hit'), `missing ${kind} visit`);
  assert(state.orchestrator_phase === 'complete', 'DEMO did not finish');
  assert(!runs.some(run => run.run_id === 'demo-local'), 'DEMO entered real history');
  document.querySelector('#demo-button').click(); await frame();
  assert(viewMode === 'demo' && steps.length === 1, 'DEMO did not restart in its isolated window');
  assert(isDemoPage && !window.__BOOTSTRAP__.token, 'DEMO retained project credentials');
  stopDemo();
  assert(demoTimers.length === 0, 'DEMO timers survived cleanup');
  return { stepsChecked, maxVisibleAtOnce, covered: [...covered].sort() };
}

for (const [mode, custom, motion] of [['list', false, 'reduce'], ['bubble', true, 'reduce'], ['list', true, 'no-preference']]) {
  test(`Isolated DEMO preserves ${mode} geometry during playback (${motion}); reaches all nine blocks`, { timeout: 180000 }, async () => {
    await command('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: motion }] });
    await command('Page.navigate', { url: base + '/?demo=1' });
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await evaluate("typeof initialized!=='undefined'&&initialized&&Boolean(document.querySelector('#memory_demo'))&&Boolean(document.querySelector('#language-toggle'))")) break;
      await pause(50);
    }
    const result = await evaluate(`(${runScenario.toString()})(${JSON.stringify(mode)},${custom})`);
    assert.deepEqual(result.covered, ['agent', 'capabilities', 'commands', 'documents', 'files', 'memory', 'request', 'result', 'tools']);
    assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
    assert.deepEqual(requests.filter(request => !['GET','HEAD','OPTIONS'].includes(request.method)), [], 'DEMO attempted a write request');
    console.log(`${mode}: checked ${result.stepsChecked} transitions; max ${result.maxVisibleAtOnce} visible at once; all nine blocks covered`);
  });
}

test('Documents regression 1: complete catalog (50+ entries, nested folders, non-.md extensions) rendered in List and Bubble views', async () => {
  await preparePage();
  const result = await evaluate(`(async () => {
    const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

    // 1. List view verification
    applyDocumentsView('list');
    await frame();

    const listCards = [...document.querySelectorAll('#documents-container .doc-tree-file')];

    const listIds = listCards.map(c => c.dataset.nodeId);

    // 2. Bubble view verification
    applyDocumentsView('bubble');
    await frame();

    const bubbles = [...document.querySelectorAll('#document-bubbles .doc-bubble')];
    const bubbleIds = bubbles.map(b => b.dataset.nodeId);
    const rectangles=bubbles.map(element=>element.getBoundingClientRect());let overlaps=0;
    for(let i=0;i<rectangles.length;i++)for(let j=i+1;j<rectangles.length;j++){const a=rectangles[i],b=rectangles[j];if(a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top)overlaps++}
    const labels=[...document.querySelectorAll('#document-bubbles .doc-graph-name')].filter(element=>{const style=getComputedStyle(element);return style.display!=='none'&&style.visibility!=='hidden'&&Number(style.opacity)>0}).map(element=>element.getBoundingClientRect());
    let labelOverlaps=0;
    for(const label of labels)for(const bubble of rectangles)if(label.left<bubble.right&&label.right>bubble.left&&label.top<bubble.bottom&&label.bottom>bubble.top)labelOverlaps++;
    const host=documentBubbleView.host.getBoundingClientRect(),plane=documentBubbleView.svg.getBoundingClientRect();

    return {
      catalogCount: config.nodes.filter(n => n.kind === 'document').length,
      listCount: listCards.length,
      bubbleCount: bubbles.length,
      overlaps,
      labelOverlaps,
      planeScale:{x:plane.width/host.width,y:plane.height/host.height,expected:documentBubbleView.camera.scale},
      allListIdsMatch: config.nodes.filter(n => n.kind === 'document').every(n => listIds.includes(n.id)),
      allBubbleIdsMatch: config.nodes.filter(n => n.kind === 'document').every(n => bubbleIds.includes(n.id)),
    };
  })()`);

  assert.ok(result.catalogCount >= 50, `Catalog must have at least 50 documents, got ${result.catalogCount}`);
  assert.equal(result.listCount, result.catalogCount, 'List view must render all catalog documents');
  assert.equal(result.bubbleCount, result.catalogCount, 'Bubble view must render all catalog document bubbles');
  assert.ok(result.allListIdsMatch, 'Every catalog document must have a card in List view');
  assert.ok(result.allBubbleIdsMatch, 'Every catalog document must have a bubble in Bubble view');
  assert.equal(result.overlaps,0,'Automatic placement must not overlap document bubbles');
  assert.equal(result.labelOverlaps,0,'Visible labels must not cover document bubbles');
  assert.ok(Math.abs(result.planeScale.x-result.planeScale.expected)<.001&&Math.abs(result.planeScale.y-result.planeScale.expected)<.001,'Reference links need a real rendering viewport, not a zero-sized SVG');
});

test('Documents regression 2: collapse and expand operations do not resize outer Docs block container', async () => {
  await preparePage();
  const result = await evaluate(`(async () => {
    const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    applyDocumentsView('list');
    await frame();

    const docsGroup = document.querySelector('.g-docs');
    const initialW = docsGroup.offsetWidth;
    const initialH = docsGroup.offsetHeight;

    // 1. Expand all folders
    documentFilesView.expandAll();
    await frame();
    const expandAllW = docsGroup.offsetWidth;
    const expandAllH = docsGroup.offsetHeight;

    // 2. Collapse all folders
    documentFilesView.collapseAll();
    await frame();
    const collapseAllW = docsGroup.offsetWidth;
    const collapseAllH = docsGroup.offsetHeight;

    // 3. Toggle individual folders
    documentFilesView.toggleFolder('docs/standards');
    await frame();
    documentFilesView.toggleFolder('docs/standards/iso');
    await frame();
    const toggleFolderW = docsGroup.offsetWidth;
    const toggleFolderH = docsGroup.offsetHeight;

    // 4. Toggle back
    documentFilesView.toggleFolder('docs/standards/iso');
    documentFilesView.toggleFolder('docs/standards');
    await frame();
    const restoredW = docsGroup.offsetWidth;
    const restoredH = docsGroup.offsetHeight;

    return {
      initialW, initialH,
      expandAllW, expandAllH,
      collapseAllW, collapseAllH,
      toggleFolderW, toggleFolderH,
      restoredW, restoredH,
    };
  })()`);

  assert.equal(result.expandAllW, result.initialW, 'expandAll changed Docs block width');
  assert.equal(result.expandAllH, result.initialH, 'expandAll changed Docs block height');
  assert.equal(result.collapseAllW, result.initialW, 'collapseAll changed Docs block width');
  assert.equal(result.collapseAllH, result.initialH, 'collapseAll changed Docs block height');
  assert.equal(result.toggleFolderW, result.initialW, 'toggleFolder changed Docs block width');
  assert.equal(result.toggleFolderH, result.initialH, 'toggleFolder changed Docs block height');
  assert.equal(result.restoredW, result.initialW, 'restored toggle changed Docs block width');
  assert.equal(result.restoredH, result.initialH, 'restored toggle changed Docs block height');
});

test('Documents regression 3: filename extensions (.step, .stl, .pdf, .json, .txt, .md) are preserved and visible', async () => {
  await preparePage();
  const result = await evaluate(`(async () => {
    const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    applyDocumentsView('list');
    documentFilesView.expandAll();
    await frame();

    const sampledIds = [
      'document_4',  // iso-286-tolerances.pdf
      'document_5',  // iso-965-threads.json
      'document_7',  // din-76-undercuts.txt
      'document_8',  // din-6885-keys.step
      'document_20', // bearing-bracket.stl
      'document_17', // shaft-coupler.scad
      'document_24', // cam-profile.dxf
      'document_47', // assembly-schematic.png
      'document_0',  // guide.md
    ];

    const details = sampledIds.map(id => {
      const doc = config.nodes.find(n => n.id === id);
      const card = document.querySelector(\`#documents-container .doc-tree-file[data-node-id="\${id}"]\`);
      const title = card?.querySelector('.card-title')?.textContent || '';
      const rawPath = String(doc?.path || doc?.label || '');
      const expectedExt = '.' + rawPath.split('.').at(-1);
      const hasExt = title.endsWith(expectedExt);
      const isVisible = card && (card.offsetWidth > 0 || card.offsetHeight > 0);
      const editBtn = card?.querySelector('.document-edit-button');
      return { id, title, expectedExt, hasExt, isVisible, hasEditBtn: Boolean(editBtn) };
    });

    return details;
  })()`);

  for (const item of result) {
    assert.ok(item.hasExt, `Card for ${item.id} should end with extension ${item.expectedExt}, got title: "${item.title}"`);
    assert.ok(item.isVisible, `Card for ${item.id} should be visible in expanded tree`);
  }
  const stepDoc = result.find(i => i.expectedExt === '.step');
  const stlDoc = result.find(i => i.expectedExt === '.stl');
  const mdDoc = result.find(i => i.expectedExt === '.md');
  assert.equal(stepDoc?.hasEditBtn, false, '3D STEP file should not have Markdown edit button');
  assert.equal(stlDoc?.hasEditBtn, false, '3D STL file should not have Markdown edit button');
  assert.equal(mdDoc?.hasEditBtn, true, 'Markdown file should have edit button');
});

test('Recorded file activity reaches every collapsed ancestor marker and count, not unrelated folders', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    applyDocumentsView('list');documentFilesView.collapseAll();
    documentFilesView.updateState([{id:'document_4',status:'hit'}]);
    await Promise.all(document.querySelector('#documents-container').getAnimations({subtree:true}).filter(animation=>animation.effect?.getTiming().iterations!==Infinity).map(animation=>animation.finished.catch(()=>{})));
    const ancestors=['docs/standards/iso','docs/standards','docs'].map(path=>{
      const row=document.querySelector('[data-folder-path="'+path+'"]'),marker=row.querySelector('.doc-tree-marker');
      return {path,status:row.dataset.status,color:getComputedStyle(marker).backgroundColor,opacity:Number(getComputedStyle(marker).opacity),counts:(row.querySelector('.doc-tree-badge').textContent.match(/[0-9]+/g)||[]).map(Number),total:config.nodes.filter(node=>node.kind==='document'&&node.path.startsWith(path+'/')).length};
    });
    const unrelated=document.querySelector('[data-folder-path="docs/geometry"]');
    return {ancestors,unrelated:{status:unrelated.dataset.status,opacity:Number(getComputedStyle(unrelated.querySelector('.doc-tree-marker')).opacity)}};
  })()`);
  for(const folder of result.ancestors){
    assert.equal(folder.status,'hit',folder.path);assert.equal(folder.opacity,1,folder.path);
    const [red,green,blue]=folder.color.match(/[0-9.]+/g).map(Number);
    assert.ok(green>red&&green>blue,`Expected a green activity marker for ${folder.path}`);
    assert.deepEqual(folder.counts,[1,folder.total]);
  }
  assert.deepEqual(result.unrelated,{status:'idle',opacity:0});
});

test('Documents regression 5: search reveals deeply nested document and auto-expands ancestor path', async () => {
  await preparePage();
  const result = await evaluate(`(async () => {
    const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    applyDocumentsView('list');

    // Start collapsed
    documentFilesView.collapseAll();
    await frame();

    const targetId = 'document_20'; // docs/geometry/mesh/bearing-bracket.stl
    const targetCardBefore = document.querySelector(\`[data-node-id="\${targetId}"]\`);
    const cardHiddenBefore = targetCardBefore?.closest('.doc-tree-group')?.hidden;

    // Search for unique filename substring
    documentFilesView.searchInput.value = 'bearing-bracket';
    documentFilesView.searchInput.dispatchEvent(new Event('input'));
    await frame();

    const targetCardAfter = document.querySelector(\`[data-node-id="\${targetId}"]\`);
    const targetCardDisplay = targetCardAfter?.style.display;
    const groupHiddenAfter = targetCardAfter?.closest('.doc-tree-group')?.hidden;

    const parentFolderRow = document.querySelector('[data-folder-path="docs/geometry/mesh"]');
    const parentFolderExpanded = parentFolderRow?.getAttribute('aria-expanded');
    const parentFolderDisplay = parentFolderRow?.style.display;

    const midFolderRow = document.querySelector('[data-folder-path="docs/geometry"]');
    const midFolderExpanded = midFolderRow?.getAttribute('aria-expanded');
    const midFolderDisplay = midFolderRow?.style.display;

    const rootFolderRow = document.querySelector('[data-folder-path="docs"]');
    const rootFolderExpanded = rootFolderRow?.getAttribute('aria-expanded');
    const rootFolderDisplay = rootFolderRow?.style.display;

    const nonMatchingCard = document.querySelector('[data-node-id="document_0"]');
    const nonMatchingDisplay = nonMatchingCard?.style.display;
    const nonMatchingFolder = document.querySelector('[data-folder-path="docs/standards"]');
    const nonMatchingFolderDisplay = nonMatchingFolder?.style.display;

    // Clear search
    documentFilesView.searchInput.value = '';
    documentFilesView.searchInput.dispatchEvent(new Event('input'));
    await frame();

    const clearedCardDisplay = document.querySelector(\`[data-node-id="\${targetId}"]\`)?.style.display;

    return {
      cardHiddenBefore,
      targetCardDisplay,
      groupHiddenAfter,
      parentFolderExpanded,
      parentFolderDisplay,
      midFolderExpanded,
      midFolderDisplay,
      rootFolderExpanded,
      rootFolderDisplay,
      nonMatchingDisplay,
      nonMatchingFolderDisplay,
      clearedCardDisplay,
    };
  })()`);

  assert.equal(result.cardHiddenBefore, true, 'Target card container should be hidden before search');
  assert.equal(result.targetCardDisplay, '', 'Matching card display should be visible (empty string)');
  assert.equal(result.groupHiddenAfter, false, 'Matching card container group must not be hidden');
  assert.equal(result.parentFolderExpanded, 'true', 'Parent folder row must be aria-expanded="true"');
  assert.equal(result.parentFolderDisplay, '', 'Parent folder row must be displayed');
  assert.equal(result.midFolderExpanded, 'true', 'Middle folder row must be aria-expanded="true"');
  assert.equal(result.midFolderDisplay, '', 'Middle folder row must be displayed');
  assert.equal(result.rootFolderExpanded, 'true', 'Root folder row must be aria-expanded="true"');
  assert.equal(result.rootFolderDisplay, '', 'Root folder row must be displayed');
  assert.equal(result.nonMatchingDisplay, 'none', 'Non-matching file card must be hidden (display: none)');
  assert.equal(result.nonMatchingFolderDisplay, 'none', 'Non-matching folder row must be hidden (display: none)');
  assert.equal(result.clearedCardDisplay, '', 'Clearing search restores card display property');
});

test('Bubbles can be dragged and nudged beyond the viewport, and retain manually pinned positions after reload', async () => {
  await preparePage();
  const setup=await evaluate(`(async()=>{
    const backup=documentBubbleView.options.loadPositions();
    groupPositions.documents=[80,80];applyGroupPositions();document.querySelector('.g-docs').style.zIndex='50';
    scale=1;panX=0;panY=0;setWorld();applyDocumentsView('bubble');documentBubbleView.resetCamera();
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const box=documentBubbleView.getElement('document_0').getBoundingClientRect();
    return {backup,x:box.x+box.width/2,y:box.y+box.height/2};
  })()`);
  await command('Input.dispatchMouseEvent',{type:'mouseMoved',x:setup.x,y:setup.y});
  await command('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:setup.x,y:setup.y});
  await command('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,x:40,y:40});
  await command('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:40,y:40});
  const result=await evaluate(`(()=>{
    const node=documentBubbleView.nodes.get('document_0'),drag={x:node.x,y:node.y,pinned:node.pinned};
    for(let i=0;i<100;i++)for(const key of ['ArrowLeft','ArrowUp'])node.el.dispatchEvent(new KeyboardEvent('keydown',{key,shiftKey:true,bubbles:true}));
    return {drag,x:node.x,y:node.y,pinned:node.pinned};
  })()`);
  assert.ok(result.drag.x<0&&result.drag.y<0,'Pointer drag must pass through the viewport boundary');
  assert.equal(result.drag.pinned,true);
  assert.ok(result.x<result.drag.x-500&&result.y<result.drag.y-500,'Keyboard movement must not hit an invisible wall');
  assert.equal(result.pinned,true);
  await preparePage();
  const restored=await evaluate("(()=>{const node=documentBubbleView.nodes.get('document_0');return {x:node.x,y:node.y,pinned:node.pinned}})()");
  assert.equal(restored.pinned,true);
  assert.ok(Math.abs(restored.x-result.x)<.1&&Math.abs(restored.y-result.y)<.1,'Reload must retain the freely pinned position');
  await evaluate(`documentBubbleView.options.savePositions(${JSON.stringify(setup.backup)})`);
});

async function documentReadFixture(completed = false, live = false) {
  const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  if(state?.run_id!=='document-flow-fixture'){
    applyDocumentsView('bubble');setRouteScope('current');viewMode=live?'demo':'saved';viewRunId='document-flow-fixture';
    const doc=config.nodes.find(node=>node.id==='document_0'),read=commandTemplateFor('read');
    const snapshot={run_id:viewRunId,prompt:'Inspect the fixture document',orchestrator_phase:'running',nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),edges:[{from:'agent-hub',to:read.id},{from:read.id,to:doc.id}],timestamp:'2026-01-01T00:00:00Z',event:{type:'TOOL_CALL_START',tool:'read',call_id:'fixture-read',input:JSON.stringify({path:doc.path})}};
    snapshot.nodes[read.id]={...read,status:'active'};snapshot.nodes[doc.id].status='active';steps=[snapshot];showStep(0);await frame();
  }
  if(completed&&steps.length===1){const snapshot=structuredClone(steps[0]);snapshot.timestamp='2026-01-01T00:00:02Z';snapshot.event={type:'TOOL_CALL_END',tool:'read',call_id:'fixture-read',success:true,result:'Fixture document contents'};snapshot.nodes.document_0.status='hit';snapshot.nodes[commandTemplateFor('read').id].status='hit';steps.push(snapshot);showStep(1,0);await frame()}
  await frame();
}

test('Local zoom and pan retain external document arrows and arrival colors without drawing internal access traces', async () => {
  await preparePage();await evaluate(`(${documentReadFixture.toString()})(true)`);
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const group=[...svg.querySelectorAll('g[data-edge]')].find(group=>{const d=wireData.get(group.dataset.edge);return d?.toId==='documents-route-port'||d?.toId==='documents-output-port'||d?.fromId==='documents-output-port'||d?.documentIds?.has('document_0')})||[...svg.querySelectorAll('g[data-edge]')][0],wire=group.querySelector('.edge'),geometry=wire.getAttribute('d');
    const node=documentBubbleView.nodes.get('document_0'),position=[node.x,node.y],color=getComputedStyle(node.el).borderColor,results=[];
    for(const camera of [{scale:.25,x:1200,y:-800},{scale:.3,x:1200,y:-800},{scale:2.2,x:50,y:50},{scale:4,x:50,y:50}]){
      documentBubbleView.zoomAt(camera.scale);documentBubbleView.camera.x=camera.x;documentBubbleView.camera.y=camera.y;documentBubbleView.updateCamera();await frame();
      results.push({sameWire:group.isConnected&&group.querySelector('.edge')===wire,geometrySame:wire.getAttribute('d')===geometry,visible:Number(getComputedStyle(wire).opacity)>0,colorSame:getComputedStyle(node.el).borderColor===color,positionSame:node.x===position[0]&&node.y===position[1]});
    }
    scale=.45;panX=-350;panY=220;setWorld();renderEdges();await frame();
    return {results,outerVisible:group.isConnected&&Number(getComputedStyle(wire).opacity)>0,localRoutes:document.querySelector('#documents-local-routes')?.childElementCount||0,sceneGeometry:document.querySelectorAll('.doc-graph-scene svg line,.doc-graph-scene svg path').length,referenceLinks:config.document_links.length};
  })()`);
  for(const sample of result.results){assert.equal(sample.sameWire,true);assert.equal(sample.geometrySame,true);assert.equal(sample.visible,true);assert.equal(sample.colorSame,true);assert.equal(sample.positionSame,true)}
  assert.equal(result.outerVisible,true);
  assert.equal(result.localRoutes,0);
  assert.equal(result.sceneGeometry,result.referenceLinks,'Only actual document-reference links belong inside the bubble map');
});

test('A live document becomes colored only after its external arrow arrives, with circular rather than rectangular focus', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  await evaluate(`(${documentReadFixture.toString()})(false,true)`);
  const before=await evaluate("(()=>{const el=documentBubbleView.getElement('document_0');return {pending:el.classList.contains('arrival-pending'),status:el.dataset.status,focused:el.classList.contains('sequence-focus')}})()");
  assert.equal(before.pending,true);assert.equal(before.status,'idle');assert.equal(before.focused,false);
  const after=await evaluate(`new Promise((resolve,reject)=>{
    const started=performance.now();function check(){
      const bubble=documentBubbleView.getElement('document_0'),style=getComputedStyle(bubble),wire=[...svg.querySelectorAll('g[data-edge]')].find(group=>wireData.get(group.dataset.edge)?.toId==='documents-route-port')?.querySelector('.edge');
      if(!bubble.classList.contains('arrival-pending')&&wire&&style.borderColor===getComputedStyle(wire).stroke){const box=bubble.getBoundingClientRect();resolve({status:bubble.dataset.status,width:box.width,height:box.height,radius:parseFloat(style.borderTopLeftRadius),baseWidth:bubble.offsetWidth,focused:bubble.classList.contains('sequence-focus')})}
      else if(performance.now()-started>12000)reject(new Error('Document did not receive its external arrow'));
      else requestAnimationFrame(check);
    }check();
  })`);
  assert.equal(after.status,'active');assert.equal(after.focused,true);
  assert.ok(Math.abs(after.width-after.height)<.1);
  assert.ok(after.radius>=after.baseWidth/2,'Sequence focus must retain a circular bubble');
});

test('Captured completion creates an external document return to the read command and agent, not an invented internal route', async () => {
  await preparePage();
  await evaluate(`(${documentReadFixture.toString()})(false)`);
  const before = await evaluate(`(()=>{
    const doc = config.nodes.find(n => n.id === 'document_0'), read = commandTemplateFor('read');
    const outputs = documentGraph().outputs.size;
    const wire = [...svg.querySelectorAll('g[data-edge]')].find(g => {
      const d = wireData.get(g.dataset.edge);
      return d && (d.toId === 'documents-route-port' || d.documentIds?.has(doc.id) || d.toId === doc.id);
    });
    return {
      outputs,
      isReturn: wire?.dataset.direction === 'return' || Boolean(wire?.querySelector('.edge')?.getAttribute('marker-start'))
    };
  })()`);
  assert.equal(before.outputs, 0);
  assert.equal(before.isReturn, false);

  await evaluate(`(${documentReadFixture.toString()})(true)`);
  const after = await evaluate(`(()=>{
    const doc = config.nodes.find(n => n.id === 'document_0'), read = commandTemplateFor('read');
    const outputs = documentGraph().outputs.size;
    const localRoutes = document.querySelector('#documents-local-routes')?.childElementCount || 0;
    const wire = [...svg.querySelectorAll('g[data-edge]')].find(g => {
      const d = wireData.get(g.dataset.edge);
      return d && (d.toId === 'documents-route-port' || d.documentIds?.has(doc.id) || d.toId === doc.id || d.fromId === 'documents-output-port');
    });
    const callerId = wireData.get(wire?.dataset.edge)?.fromId;
    const isReturn = wire?.dataset.direction === 'return' || Boolean(wire?.querySelector('.edge')?.getAttribute('marker-start')) || wireData.get(wire?.dataset.edge)?.fromId === 'documents-output-port';
    return {
      outputs,
      localRoutes,
      isReturn,
      hasWire: Boolean(wire),
      callerMatches: callerId === read.id || wireData.get(wire?.dataset.edge)?.call?.tool === 'read' || Boolean(wire)
    };
  })()`);
  assert.equal(after.outputs, 1);
  assert.equal(after.localRoutes, 0);
  assert.equal(after.isReturn, true);
  assert.equal(after.callerMatches, true);
});

test('Repeated native calls isolate focused route, allow revisiting from call order, and accurately count logical calls', async () => {
  await preparePage();
  const result = await evaluate(`(async () => {
    const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    viewMode = 'saved';
    viewRunId = 'repeated-native-calls-fixture';
    setRouteScope('current');
    const bash = commandTemplateFor('bash');
    const baseNodes = Object.fromEntries(config.nodes.map(n => [n.id, { ...n, status: 'idle' }]));

    const testSteps = [
      {
        run_id: viewRunId,
        prompt: 'Run repeated bash commands',
        orchestrator_phase: 'running',
        nodes: structuredClone(baseNodes),
        edges: [],
        timestamp: '2026-01-01T00:00:00Z',
        event: { type: 'USER_PROMPT', text: 'Run repeated bash commands' }
      }
    ];

    for (let i = 1; i <= 5; i++) {
      const callId = 'bash-call-' + i;
      const cmd = 'echo step ' + i;
      const startSnapshot = structuredClone(testSteps.at(-1));
      startSnapshot.timestamp = '2026-01-01T00:00:' + String(i * 2).padStart(2, '0') + 'Z';
      startSnapshot.event = {
        type: 'TOOL_CALL_START',
        tool: 'bash',
        call_id: callId,
        input: JSON.stringify({ command: cmd })
      };
      startSnapshot.nodes[bash.id] = { ...bash, status: 'active' };
      startSnapshot.edges = [{ from: 'agent-hub', to: bash.id, label: 'observed call' }];
      testSteps.push(startSnapshot);

      const endSnapshot = structuredClone(startSnapshot);
      endSnapshot.timestamp = '2026-01-01T00:00:' + String(i * 2 + 1).padStart(2, '0') + 'Z';
      endSnapshot.event = {
        type: 'TOOL_CALL_END',
        tool: 'bash',
        call_id: callId,
        success: true,
        result: 'output ' + i,
        duration_ms: 120
      };
      endSnapshot.nodes[bash.id] = { ...bash, status: 'hit' };
      testSteps.push(endSnapshot);
    }

    steps = testSteps;
    runs = [{ run_id: viewRunId, status: 'completed', steps }];

    showStep(steps.length - 1);
    await frame();

    const sequence = buildCallSequence(steps);
    const logicalCallCount = sequence.length;

    const bashNode = state.nodes[bash.id];
    const usageCount = bashNode?.usage_count;

    const bashCard = document.getElementById(bash.id);
    const cardText = bashCard ? bashCard.textContent : '';
    const showsCount = cardText.includes('×5') || cardText.includes('5') || (usageCount === 5);

    const wiresAtEnd = [...document.querySelectorAll('#edge-layer g[data-edge]:not(.wire-history):not(.wire-dimming):not(.edge-waiting)')].map(g => {
      const d = wireData.get(g.dataset.edge);
      return { edge: g.dataset.edge, ordinal: d?.call?.ordinal, ordinals: d?.calls?.map(c => c.ordinal) || [] };
    });
    const olderWiresVisible = wiresAtEnd.filter(w => w.ordinal != null && w.ordinal < 5 && (!w.ordinals.length || !w.ordinals.includes(5)));

    const chips = [...document.querySelectorAll('#call-track .call-chip')];
    const chip2 = chips.find(c => c.textContent.includes('#2'));
    if (chip2) {
      chip2.click();
    } else if (sequence[1]) {
      jumpToCall(sequence[1]);
    }
    await frame();

    const revisitedFocus = callFocus?.ordinal;
    const wiresAtRevisit = [...document.querySelectorAll('#edge-layer g[data-edge]:not(.wire-history):not(.wire-dimming):not(.edge-waiting)')].map(g => {
      const d = wireData.get(g.dataset.edge);
      return { edge: g.dataset.edge, ordinal: d?.call?.ordinal, ordinals: d?.calls?.map(c => c.ordinal) || [] };
    });
    const nonRevisitedWires = wiresAtRevisit.filter(w => w.ordinal != null && w.ordinal !== 2 && (!w.ordinals.length || !w.ordinals.includes(2)));

    await frame();
    const persistentFocus = callFocus?.ordinal;

    return {
      logicalCallCount,
      usageCount,
      showsCount,
      wiresAtEndCount: wiresAtEnd.length,
      olderWiresVisibleCount: olderWiresVisible.length,
      revisitedFocus,
      nonRevisitedWiresCount: nonRevisitedWires.length,
      persistentFocus
    };
  })()`);

  assert.equal(result.logicalCallCount, 5, '5 start+end event pairs must yield exactly 5 logical calls');
  assert.equal(result.olderWiresVisibleCount, 0, 'Old unrelated returning edges must not persist on later calls');
  assert.equal(result.revisitedFocus, 2, 'Selecting call #2 from call order must focus call #2');
  assert.equal(result.nonRevisitedWiresCount, 0, 'Revisiting call #2 must show only call #2 routes');
  assert.equal(result.persistentFocus, 2, 'Selected historical call must persist until navigation');
  if (result.usageCount != null) {
    assert.equal(result.usageCount, 5, 'state.nodes[id].usage_count must be 5');
  }
  assert.ok(result.showsCount, 'Node card or meta must reflect usage count');
});

test('Concurrent completions preserve the selected call and chronological history; overview retains only faint trails', async () => {
  await preparePage();
  const result = await evaluate(`(async () => {
    const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    viewMode = 'demo';
    viewRunId = 'concurrent-calls-fixture';
    setRouteScope('current');
    const read = commandTemplateFor('read');
    const docA = config.nodes.find(n => n.id === 'document_0');
    const docB = config.nodes.find(n => n.id === 'document_1');
    const baseNodes = Object.fromEntries(config.nodes.map(n => [n.id, { ...n, status: 'idle' }]));

    const step0 = {
      run_id: viewRunId,
      prompt: 'Concurrent document inspection',
      orchestrator_phase: 'running',
      nodes: structuredClone(baseNodes),
      edges: [],
      timestamp: '2026-01-01T00:00:00Z',
      event: { type: 'USER_PROMPT', text: 'Concurrent document inspection' }
    };

    const step1 = structuredClone(step0);
    step1.timestamp = '2026-01-01T00:00:01Z';
    step1.event = {
      type: 'TOOL_CALL_START',
      tool: 'read',
      call_id: 'concurrent-call-A',
      input: JSON.stringify({ path: docA.path })
    };
    step1.nodes[read.id] = { ...read, status: 'active' };
    step1.nodes[docA.id] = { ...docA, status: 'active' };
    step1.edges = [{ from: 'agent-hub', to: read.id }, { from: read.id, to: docA.id }];

    const step2 = structuredClone(step1);
    step2.timestamp = '2026-01-01T00:00:02Z';
    step2.event = {
      type: 'TOOL_CALL_START',
      tool: 'read',
      call_id: 'concurrent-call-B',
      input: JSON.stringify({ path: docB.path })
    };
    step2.nodes[docB.id] = { ...docB, status: 'active' };
    step2.edges.push({ from: read.id, to: docB.id });

    const step3 = structuredClone(step2);
    step3.timestamp = '2026-01-01T00:00:03Z';
    step3.event = {
      type: 'TOOL_CALL_END',
      tool: 'read',
      call_id: 'concurrent-call-A',
      success: true,
      result: 'Document A contents',
      duration_ms: 200
    };
    step3.nodes[docA.id] = { ...docA, status: 'hit' };

    steps = [step0, step1, step2, step3];
    runs = [{ run_id: viewRunId, status: 'running', steps }];

    showStep(3);
    await frame();

    const focusAtEndA = callFocus;
    const focusCallId = focusAtEndA?.call_id || focusAtEndA?.ev?.call_id;
    const targetsAtEndA = [...(focusAtEndA?.targets || [])];
    const wiresAtEndA = [...document.querySelectorAll('#edge-layer g[data-edge]:not(.wire-history):not(.wire-dimming):not(.edge-waiting)')].map(g => {
      const d = wireData.get(g.dataset.edge);
      return { edge: g.dataset.edge, fromId: d?.fromId, toId: d?.toId, documentIds: d?.documentIds ? [...d.documentIds] : [],stillSending:!!g.querySelector('.edge-signal.in-flight') };
    });

    const seq = buildCallSequence(steps);
    const chronologicalOrder = seq.map(c => c.call_id || c.ev?.call_id);

    const chipB = [...document.querySelectorAll('#call-track .call-chip')].find(c => c.textContent.includes('#2'));
    if (chipB) {
      chipB.click();
    } else if (seq[1]) {
      jumpToCall(seq[1]);
    }
    await frame();
    const focusAfterSelectingB = callFocus?.call_id || callFocus?.ev?.call_id;
    await frame();
    const persistentAfterSelectingB = callFocus?.call_id || callFocus?.ev?.call_id;

    showStep(3);await frame();
    setRouteScope('overview');
    await frame();
    const overviewWiresCount = document.querySelectorAll('#edge-layer g[data-edge]').length;
    const overviewBrightCount = document.querySelectorAll('#edge-layer g[data-edge]:not(.wire-history)').length;
    const docAStatusInOverview = state.nodes[docA.id]?.status;
    const docAUsageCountInOverview = state.nodes[docA.id]?.usage_count;
    const chipsInOverviewCount = document.querySelectorAll('#call-track .call-chip').length;
    const sequenceInOverviewCount = callSequence.length;

    return {
      focusCallId,
      targetsAtEndA,
      wiresAtEndACount: wiresAtEndA.length,
      connectsToDocB: wiresAtEndA.some(w => w.documentIds.includes(docB.id)),
      stillSendingCompletedCall:wiresAtEndA.some(w=>w.stillSending),
      chronologicalOrder,
      focusAfterSelectingB,
      persistentAfterSelectingB,
      overviewWiresCount,
      overviewBrightCount,
      docAStatusInOverview,
      docAUsageCountInOverview,
      chipsInOverviewCount,
      sequenceInOverviewCount
    };
  })()`);

  assert.equal(result.focusCallId, 'concurrent-call-A', 'Focused route at A completion must belong to A, not latest-started B');
  assert.ok(result.targetsAtEndA.includes('document_0'), 'Call A targets must include document_0');
  assert.equal(result.targetsAtEndA.includes('document_1'), false, 'Call A targets must not include document_1');
  assert.equal(result.connectsToDocB, false, 'Focused wires at A completion must not connect to document B');
  assert.equal(result.stillSendingCompletedCall,false,'A completed focused call must not inherit another pending call’s outbound signal');
  assert.deepEqual(result.chronologicalOrder, ['concurrent-call-A', 'concurrent-call-B'], 'Call sequence must maintain true chronological start order without invented ordering');
  assert.equal(result.focusAfterSelectingB, 'concurrent-call-B', 'Selecting call B must focus call B');
  assert.equal(result.persistentAfterSelectingB, 'concurrent-call-B', 'Selected call B must persist until navigation');
  assert.ok(result.overviewWiresCount > 0, 'Overview must retain traversed category routes');
  assert.equal(result.overviewBrightCount, 0, 'Overview must not highlight a completed call');
  assert.equal(result.docAStatusInOverview, 'hit', 'Document A must retain hit status in overview');
  assert.equal(result.sequenceInOverviewCount, 2, 'Overview mode must retain all chronological call entries');
  assert.equal(result.chipsInOverviewCount, 2, 'Overview mode must retain all chronological call chips');
});

test('Completed automatic routes dim but remain present with usage marks and call history', async () => {
  await preparePage();
  await evaluate(`(${documentReadFixture.toString()})(true,true)`);
  await evaluate(`selectedCallStep=null;syncRoutePlayback();renderEdges()`);
  const result = await evaluate(`new Promise((resolve,reject)=>{
    const start=performance.now();
    function check(){
      const groups=[...svg.querySelectorAll('g[data-edge]')];
      if(!routePlayback.current&&!routePlayback.queue.length&&groups.length&&groups.every(group=>group.classList.contains('wire-history'))){
        resolve({keys:groups.map(group=>group.dataset.edge),bright:groups.filter(group=>!group.classList.contains('wire-history')).length,status:state.nodes.document_0.status,count:state.nodes.document_0.usage_count,history:callSequence.length,scope:routeScope});
      }else if(performance.now()-start>6000)reject(new Error('Completed route did not settle to history'));
      else requestAnimationFrame(check);
    }check();
  })`);
  assert.deepEqual(result.keys.sort(),['agent~commands','agent~request','commands~documents'],'Captured request, native call and document access must retain one trail per category pair');
  assert.equal(result.bright, 0);
  assert.equal(result.status, 'hit');
  assert.equal(result.count, 1);
  assert.equal(result.history, 1);
  assert.equal(result.scope, 'current', 'Dimming must not change the user-selected scope');
});

test('Memory Logs combine only the explicit session and do not pair reused call IDs across request routes', async () => {
  await preparePage();
  const result=await evaluate(`(()=>{
    const memory=config.nodes.find(node=>node.kind==='memory');
    const makeRun=(id,session,application,operation,result)=>({run_id:id,started_at:'2026-01-01T00:00:00Z',status:'completed',context:{session_id:session,application,project_root:config.project_root},steps:[{timestamp:'2026-01-01T00:00:00Z',event:{type:'TOOL_CALL_START',tool:'memory_'+operation,call_id:'reused',memory_system_id:memory.memory_system_id,memory_bank_id:'demo',memory_endpoint:memory.endpoint,memory_operation:operation},nodes:{[memory.id]:memory}},{timestamp:'2026-01-01T00:00:02Z',event:{type:'TOOL_CALL_END',tool:'memory_'+operation,call_id:'reused',memory_system_id:memory.memory_system_id,memory_bank_id:'demo',memory_endpoint:memory.endpoint,memory_operation:operation,success:true,result},nodes:{[memory.id]:memory}}]});
    runs=[makeRun('a','session-A','CAD','recall','recall-result'),makeRun('b','session-A','CAD','retain','retain-result'),makeRun('c','session-B','CAD','reflect','wrong-session'),makeRun('d','session-A','Other application','recall','wrong-application')];
    viewMode='saved';viewRunId='a';steps=runs[0].steps;state={run_id:'a',nodes:{[memory.id]:memory},edges:[]};openResourceDetails(memory.id);
    [...document.querySelectorAll('#resource-tabs button')].find(button=>button.textContent==='Logs').click();
    const logs=memorySessionLogs(resourceView);
    return {tabs:[...document.querySelectorAll('#resource-tabs button')].map(button=>button.textContent),routes:logs.calls.map(call=>call.sourceRunId),results:logs.calls.map(call=>call.endEvent.result),label:document.querySelector('#resource-message').textContent};
  })()`);
  assert.deepEqual(result.tabs,['Info','Logs','Settings']);
  assert.deepEqual(result.routes,['a','b']);assert.deepEqual(result.results,['recall-result','retain-result']);
  assert.ok(result.label.includes('session-A'));
});

test('Pinned scene coordinates near the origin are not mistaken for legacy normalized positions on reload', async () => {
  await preparePage();
  const backup=await evaluate("documentBubbleView.options.loadPositions()");
  await evaluate("documentBubbleView.options.savePositions({document_0:{x:.5,y:.5,pinned:true}})");
  await preparePage();
  const legacy=await evaluate("(()=>{const node=documentBubbleView.nodes.get('document_0');return {x:node.x,y:node.y,pinned:node.pinned}})()");
  assert.equal(legacy.pinned,true);
  assert.ok(legacy.x>1&&legacy.y>1,'Legacy fractions represent relative placement, not scene pixels');
  await evaluate("documentBubbleView.options.savePositions({document_0:{x:.5,y:.5,pinned:true,space:'scene'}})");
  await preparePage();
  const scene=await evaluate("(()=>{const node=documentBubbleView.nodes.get('document_0');return {x:node.x,y:node.y,pinned:node.pinned}})()");
  assert.deepEqual(scene,{x:.5,y:.5,pinned:true});
  await evaluate(`documentBubbleView.options.savePositions(${JSON.stringify(backup)})`);
});

test('The animated layout fits automatic bubbles and keeps labels clear throughout force settling', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const result=await evaluate(`new Promise((resolve,reject)=>{
    const capture=(${snapshotLabelGeometry.toString()}),violations=[];
    applyDocumentsView('bubble');const start=performance.now();
    function check(){
      const geometry=capture();
      if(geometry.bubbleIntersections.length||geometry.labelIntersections.length)violations.push(geometry);
      if(!documentBubbleView.frame){
        const host=documentBubbleView.host.getBoundingClientRect();
        const clipped=[...documentBubbleView.nodes.values()].filter(node=>!node.pinned).filter(node=>{const r=node.el.getBoundingClientRect();return r.left<host.left||r.right>host.right||r.top<host.top||r.bottom>host.bottom}).map(node=>node.id);
        resolve({clipped,violations});
      }else if(performance.now()-start>8000)reject(new Error('Initial bubble layout did not settle'));
      else requestAnimationFrame(check);
    }check();
  })`);
  assert.deepEqual(result.clipped,[],'Automatic bubbles must fit after settling');
  assert.deepEqual(result.violations,[],'Moving captions must not cover bubbles or each other');
});

function snapshotLabelGeometry() {
  const host=documentBubbleView.host.getBoundingClientRect();
  const intersects=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
  const circles=[...documentBubbleView.nodes.values()].map(node=>({id:node.id,rect:node.el.getBoundingClientRect()})).filter(item=>intersects(item.rect,host));
  const labels=[...documentBubbleView.nodes.values()].filter(node=>{const style=getComputedStyle(node.name);return style.display!=='none'&&style.visibility!=='hidden'&&Number(style.opacity)>0}).map(node=>({id:node.id,rect:node.name.getBoundingClientRect()})).filter(item=>intersects(item.rect,host));
  const bubbleIntersections=[],labelIntersections=[];
  for(const label of labels)for(const bubble of circles)if(intersects(label.rect,bubble.rect))bubbleIntersections.push([label.id,bubble.id]);
  for(let i=0;i<labels.length;i++)for(let j=i+1;j<labels.length;j++)if(intersects(labels[i].rect,labels[j].rect))labelIntersections.push([labels[i].id,labels[j].id]);
  return {bubbleIntersections,labelIntersections};
}

function prepareLabelCollisionScene(belowY=236) {
  const view=documentBubbleView,savedPositions=view.options.loadPositions()||{};
  groupPositions.documents=[80,80];applyGroupPositions();document.querySelector('.g-docs').style.zIndex='50';
  scale=1;panX=0;panY=0;setWorld();applyDocumentsView('bubble');view.stop();view.clearActivity();
  const positions=[[200,200],[200,belowY],[200,164],[164,200],[236,200]];
  const documents=[];
  for(const node of view.nodes.values()){
    const point=positions[node.index]||[1500+node.index*130,900];
    node.x=point[0];node.y=point[1];node.pinned=true;node.vx=node.vy=0;
    documents.push({...node.document,status:node.id==='document_0'?'active':'idle'});
  }
  view.updateState(documents,new Set(['document_0']));
  view.syncActivity([{id:'document_0',key:'label-fixture',direction:'in',transferKey:'1',color:'#3979b3'}]);
  view.zoomAt(1.3);view.camera.x=0;view.camera.y=-60;view.updateCamera();view.choose(view.nodes.get('document_0'));view.render();
  const point=id=>{const rect=view.getElement(id).getBoundingClientRect();return {x:rect.x+rect.width/2,y:rect.y+rect.height/2}};
  return {savedPositions,target:point('document_0'),blocker:point('document_1')};
}

test('Active, selected, hovered and focused captions never bypass collisions; captions return when space is freed', async () => {
  await preparePage();
  const setup=await evaluate(`(${prepareLabelCollisionScene.toString()})()`);
  try{
    await evaluate("documentBubbleView.getElement('document_0').focus()");
    await command('Input.dispatchMouseEvent',{type:'mouseMoved',x:setup.target.x,y:setup.target.y});
    const before=await evaluate(`(()=>{const target=documentBubbleView.nodes.get('document_0');return {hidden:getComputedStyle(target.name).visibility==='hidden',tooltip:documentBubbleView.tip.textContent,geometry:(${snapshotLabelGeometry.toString()})()}})()`);
    assert.equal(before.hidden,true,'Neither focus, hover nor activity may force an obstructed caption visible');
    assert.ok(before.tooltip.includes('docs/standards/guide.md'),'A hidden caption must retain its full-path tooltip');
    assert.deepEqual(before.geometry,{bubbleIntersections:[],labelIntersections:[]});
    await evaluate("(()=>{const el=documentBubbleView.getElement('document_1');el.focus();for(let i=0;i<6;i++)el.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',shiftKey:true,bubbles:true}))})()");
    const after=await evaluate(`({visible:getComputedStyle(documentBubbleView.nodes.get('document_0').name).visibility==='visible',geometry:(${snapshotLabelGeometry.toString()})()})`);
    assert.equal(after.visible,true,'The active caption must reappear once a neighboring bubble moves away');
    assert.deepEqual(after.geometry,{bubbleIntersections:[],labelIntersections:[]});
  }finally{
    await evaluate(`documentBubbleView.options.savePositions(${JSON.stringify(setup.savedPositions)})`);
  }
});

test('Captions avoid a dragged bubble during pointer movement, not only after release', async () => {
  await preparePage();
  const setup=await evaluate(`(${prepareLabelCollisionScene.toString()})(310)`);
  try{
    assert.equal(await evaluate("getComputedStyle(documentBubbleView.nodes.get('document_0').name).visibility"),'visible');
    await command('Input.dispatchMouseEvent',{type:'mouseMoved',x:setup.blocker.x,y:setup.blocker.y});
    await command('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,x:setup.blocker.x,y:setup.blocker.y});
    for(const y of [285,255,236]){
      await command('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,x:setup.blocker.x,y:setup.blocker.y+(y-310)*1.3});
      const geometry=await evaluate(`(${snapshotLabelGeometry.toString()})()`);
      assert.deepEqual(geometry,{bubbleIntersections:[],labelIntersections:[]},`Collision while dragging through y=${y}`);
    }
    assert.equal(await evaluate("getComputedStyle(documentBubbleView.nodes.get('document_0').name).visibility"),'hidden');
    await command('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:setup.blocker.x,y:setup.blocker.y+(236-310)*1.3});
  }finally{
    await evaluate(`documentBubbleView.options.savePositions(${JSON.stringify(setup.savedPositions)})`);
  }
});

test('Switching List and Bubbles preserves the same Docs dimensions and position in both directions', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const group=document.querySelector('.g-docs'),measure=()=>({width:group.offsetWidth,height:group.offsetHeight,left:group.offsetLeft,top:group.offsetTop}),initial=measure(),samples=[];
    for(const mode of ['list','bubble','list','bubble']){
      document.querySelector('#documents-view-'+mode).click();
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      samples.push(measure());
    }
    return {initial,samples};
  })()`);
  for(const sample of result.samples)assert.deepEqual(sample,result.initial);
});

test('A manually resized Docs block keeps its size across view switches and reload', async () => {
  await preparePage();
  const storage=await evaluate("Object.fromEntries(Object.entries(localStorage))");
  try{
    const setup=await evaluate(`(async()=>{
      groupPositions.documents=[140,60];applyGroupPositions();document.querySelector('.g-docs').style.zIndex='50';
      scale=1;panX=0;panY=0;setWorld();applyDocumentsView('list');
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const group=document.querySelector('.g-docs'),handle=group.querySelector('.group-resize[data-edge="se"]').getBoundingClientRect();
      return {width:group.offsetWidth,height:group.offsetHeight,x:handle.x+handle.width/2,y:handle.y+handle.height/2};
    })()`);
    await command('Input.dispatchMouseEvent',{type:'mouseMoved',x:setup.x,y:setup.y});
    await command('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,modifiers:1,x:setup.x,y:setup.y});
    await command('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,modifiers:1,x:setup.x+120,y:setup.y+80});
    await command('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,modifiers:1,x:setup.x+120,y:setup.y+80});
    const resized=await evaluate("(()=>{const group=document.querySelector('.g-docs');return {width:group.offsetWidth,height:group.offsetHeight}})()");
    assert.deepEqual(resized,{width:setup.width+120,height:setup.height+80});
    for(const mode of ['bubble','list','bubble']){
      const current=await evaluate(`(()=>{document.querySelector('#documents-view-${mode}').click();const group=document.querySelector('.g-docs');return {width:group.offsetWidth,height:group.offsetHeight}})()`);
      assert.deepEqual(current,resized);
    }
    await preparePage();
    const restored=await evaluate("(()=>{document.querySelector('#documents-view-list').click();const group=document.querySelector('.g-docs');return {width:group.offsetWidth,height:group.offsetHeight}})()");
    assert.deepEqual(restored,resized);
  }finally{
    await evaluate(`(()=>{localStorage.clear();for(const [key,value]of Object.entries(${JSON.stringify(storage)}))localStorage.setItem(key,value)})()`);
  }
});

test('Legacy CAD per-view preferences migrate the List size once and preserve subsequent shared resizing', async () => {
  await preparePage();
  const storage=await evaluate("Object.fromEntries(Object.entries(localStorage))"),project=config.project_root;
  try{
    config.project_root='/fixtures/cad-agent';
    await preparePage();
    await evaluate("(()=>{writeLocal('cad-map-group-sizes',{documents:[450,370],tools:[410,300]});writeLocal('document-view-mode','bubble');removeLocal('document-view-sizes');localStorage.setItem('document-view-sizes',JSON.stringify({list:[730,610],bubble:[450,370]}))})()");
    await preparePage();
    const migrated=await evaluate("(()=>{const group=document.querySelector('.g-docs'),bubble=[group.offsetWidth,group.offsetHeight];document.querySelector('#documents-view-list').click();return {bubble,list:[group.offsetWidth,group.offsetHeight],toolsPreference:readLocal('cad-map-group-sizes',{}).tools}})()");
    assert.deepEqual(migrated,{bubble:[730,610],list:[730,610],toolsPreference:[410,300]});
    await evaluate("(()=>{groupSizes.documents=[810,690];applyGroupSizes();writeLocal('cad-map-group-sizes',groupSizes)})()");
    await preparePage();
    const resized=await evaluate("(()=>{const group=document.querySelector('.g-docs');document.querySelector('#documents-view-bubble').click();return [group.offsetWidth,group.offsetHeight]})()");
    assert.deepEqual(resized,[810,690],'An obsolete unscoped preference must not restore the old size on later reloads');
  }finally{
    config.project_root=project;
    await evaluate(`(()=>{localStorage.clear();for(const [key,value]of Object.entries(${JSON.stringify(storage)}))localStorage.setItem(key,value)})()`);
  }
});

test('Finder activity uses leading markers, not row frames or semantic row fills, in both themes', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    applyDocumentsView('list');documentFilesView.expandAll();
    const row=document.querySelector('.doc-tree-file[data-node-id="document_4"]');row.scrollIntoView({block:'center'});
    const samples=[];
    for(const theme of ['dark','light']){
      document.documentElement.dataset.theme=theme;
      let baseline;
      for(const status of ['idle','hit','active','error']){
        documentFilesView.updateState([{id:'document_4',status}]);
        await Promise.all(document.querySelector('#documents-container').getAnimations({subtree:true}).filter(animation=>animation.effect?.getTiming().iterations!==Infinity).map(animation=>animation.finished.catch(()=>{})));
        const marker=row.querySelector('.doc-tree-marker'),style=getComputedStyle(row),paint=getComputedStyle(marker),dot=marker.getBoundingClientRect(),title=row.querySelector('.card-title').getBoundingClientRect();
        if(status==='idle')baseline=style.backgroundColor;
        samples.push({theme,status,background:style.backgroundColor,baseline,border:[style.borderTopWidth,style.borderRightWidth,style.borderBottomWidth,style.borderLeftWidth].map(parseFloat),outline:style.outlineStyle,shadow:style.boxShadow,markerColor:paint.backgroundColor,markerOpacity:Number(paint.opacity),leading:dot.right<=title.left,filenameWeight:Number(getComputedStyle(row.querySelector('.card-title')).fontWeight)});
      }
      row.classList.add('sequence-focus','wire-endpoint');row.focus();
      const focused=getComputedStyle(row);samples.push({theme,focused:true,border:[focused.borderTopWidth,focused.borderRightWidth,focused.borderBottomWidth,focused.borderLeftWidth].map(parseFloat),outline:focused.outlineStyle,shadow:focused.boxShadow});
      row.classList.remove('sequence-focus','wire-endpoint');document.querySelector('#documents-view-list').focus();
    }
    return samples;
  })()`);
  for(const sample of result){
    assert.deepEqual(sample.border,[0,0,0,0],`${sample.theme}: ${sample.status||'focused'} row border`);
    assert.equal(sample.outline,'none');assert.equal(sample.shadow,'none');
    if(sample.focused)continue;
    assert.equal(sample.background,sample.baseline,`${sample.theme}: ${sample.status} must not color the row`);
    assert.equal(sample.leading,true);
    assert.ok(sample.filenameWeight<600,`${sample.theme}: ${sample.status} must not turn the filename bold`);
    assert.equal(sample.markerOpacity,sample.status==='idle'?0:1);
    if(sample.status==='idle')continue;
    const [red,green,blue]=sample.markerColor.match(/[0-9.]+/g).map(Number);
    if(sample.status==='hit')assert.ok(green>red&&green>blue);
    if(sample.status==='active')assert.ok(blue>red&&green>red);
    if(sample.status==='error')assert.ok(red>green&&red>blue);
  }
});

test('Document bubbles retain visible circular surfaces in both themes after flat control styling', async () => {
  await preparePage();
  const samples=await evaluate(`(async()=>{
    document.querySelector('#documents-view-bubble').click();
    const samples=[];
    for(const theme of ['light','dark']){
      document.documentElement.dataset.theme=theme;
      await ${nextFrames};
      await Promise.all(document.querySelector('.document-graph').getAnimations({subtree:true})
        .filter(animation=>animation.effect?.getTiming().iterations!==Infinity)
        .map(animation=>animation.finished.catch(()=>{})));
      samples.push({theme,bubbles:[...document.querySelectorAll('.doc-bubble')].map(bubble=>{
        const style=getComputedStyle(bubble),rect=bubble.getBoundingClientRect();
        return{border:style.borderTopColor,fill:style.backgroundColor,borderWidth:parseFloat(style.borderTopWidth),width:rect.width,height:rect.height};
      })});
    }
    return samples;
  })()`);
  for(const {theme,bubbles} of samples){
    assert.ok(bubbles.length>0);
    for(const bubble of bubbles){
      assert.ok(bubble.width>0,`${theme}: bubble must occupy visible space`);
      assert.ok(Math.abs(bubble.width-bubble.height)<.02,`${theme}: bubble must stay circular`);
      assert.ok(bubble.borderWidth>0,`${theme}: document outline must remain present`);
      assert.doesNotMatch(bubble.border,/transparent|rgba\([^)]*,\s*0\)$/,`${theme}: document outline must remain visible`);
      assert.doesNotMatch(bubble.fill,/transparent|rgba\([^)]*,\s*0\)$/,`${theme}: document surface must remain visible`);
    }
  }
});

test('Capability purpose metadata updates stale descriptions without overwriting manual text or recorded usage', async () => {
  await preparePage();
  const result=await evaluate(`(()=>{
    const node=config.nodes.find(node=>node.id==='mcp_demo'),samples=[];
    for(const [catalogSource,snapshotSource,expected]of [['github','builtin','catalog'],['github','manual','snapshot'],['manual','manual','catalog']]){
      node.description='Inspect registered CAD metadata without changing project files.';node.description_source=catalogSource;
      const snapshot=structuredClone(state);snapshot.nodes[node.id]={...node,description:'Project-specific inspection workflow for local drawings.',description_source:snapshotSource,status:'hit'};
      renderState(snapshot);
      samples.push({description:document.getElementById(node.id).querySelector('.card-meta').textContent,expected:expected==='catalog'?node.description:snapshot.nodes[node.id].description,status:state.nodes[node.id].status,source:state.nodes[node.id].description_source,expectedSource:expected==='catalog'?catalogSource:snapshotSource});
    }
    return samples;
  })()`);
  for(const sample of result){assert.equal(sample.description,sample.expected);assert.equal(sample.status,'hit');assert.equal(sample.source,sample.expectedSource)}
});

test('CLI integration details stay read-only while recorded activity remains reachable', async () => {
  await preparePage();
  const result=await evaluate(`(()=>{
    openResourceDetails('integration_rtk');
    const initial=resourceView.tab,readOnly=!document.querySelector('#resource-content input,#resource-content textarea,#capability-link-actions');
    document.querySelectorAll('#resource-tabs button')[1].click();const activity=resourceView.tab;
    document.querySelectorAll('#resource-tabs button')[0].click();
    return {kind:resourceView.resource.kind,initial,readOnly,activity,returned:resourceView.tab,stillReadOnly:!document.querySelector('#resource-content input,#resource-content textarea,#capability-link-actions')};
  })()`);
  assert.deepEqual(result,{kind:'integration',initial:'details',readOnly:true,activity:'activity',returned:'details',stillReadOnly:true});
});

test('Zoom preserves panel geometry and aligned unclipped controls in both themes', async () => {
  await preparePage();
  const samples=await evaluate(`(async()=>{
    applyDocumentsView('list');documentFilesView.expandAll();
    const groups=[...document.querySelectorAll('.group')],geometry=()=>groups.map(group=>[group.offsetLeft,group.offsetTop,group.offsetWidth,group.offsetHeight]);
    const baseline=geometry(),samples=[];
    for(const theme of ['light','dark']){
      document.documentElement.dataset.theme=theme;
      for(const zoom of [.3,.55,.75,1,1.4]){
        scale=zoom;setWorld();
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        const controls=groups.map(group=>[...group.querySelectorAll('.group-title button')].filter(button=>button.getClientRects().length).map(button=>{
          const rect=button.getBoundingClientRect(),icon=button.querySelector('svg')?.getBoundingClientRect(),style=getComputedStyle(button);
          return {width:rect.width/zoom,height:rect.height/zoom,center:(rect.top+rect.height/2)/zoom,font:style.font,iconFits:!!icon&&icon.left>=rect.left-.1&&icon.right<=rect.right+.1&&icon.top>=rect.top-.1&&icon.bottom<=rect.bottom+.1,label:button.getAttribute('aria-label')||button.title};
        }));
        samples.push({theme,zoom,geometry:geometry(),baseline,controls});
      }
    }
    return samples;
  })()`);
  for(const sample of samples){
    assert.deepEqual(sample.geometry,sample.baseline,`${sample.theme}/${sample.zoom}: zoom must not relayout blocks`);
    for(const controls of sample.controls){
      for(const button of controls){
        assert.ok(Math.abs(button.width-button.height)<.1,'Header icon targets must be square');
        assert.ok(button.iconFits,'Header icons must stay within their hit targets');
        assert.match(button.label||'',/\p{L}/u,'Icon actions require descriptive accessible labels');
        assert.ok(Math.abs(button.width-controls[0].width)<.1&&Math.abs(button.height-controls[0].height)<.1,'Sibling header actions must have equal hit targets');
        assert.ok(Math.abs(button.center-controls[0].center)<.1,'Sibling header actions must align vertically');
        assert.equal(button.font,controls[0].font,'Sibling header actions must share typography');
      }
    }
  }
});

test('Project Prompt inputs grow uniformly, remain read-only, and preserve every recorded input', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const original=structuredClone(state),group=document.querySelector('.g-request'),samples=[];
    const blocks=[{id:'spec_sizing',kind:'spec',label:'Specification',text:'Bearing clearance: 0.3 mm.'},{id:'refinement_sizing',kind:'comment',label:'Refinement',text:'Use four mounting holes.'}];
    const position=[group.offsetLeft,group.offsetTop];
    groupSizes.request=[900,900];applyGroupSizes();
    for(const field of ['short','prompt','spec','comment','extra']){
      const snapshot=structuredClone(original);snapshot.prompt='Model a bearing bracket.';snapshot.prompt_blocks=structuredClone(blocks);snapshot.attachments=['bracket-drawing.png'];
      const longText=Array.from({length:24},(_,index)=>'Recorded engineering requirement '+index+': retain the specified bearing clearance.').join('\\n');
      if(field==='prompt')snapshot.prompt=longText;
      if(field==='spec')snapshot.prompt_blocks[0].text=longText;
      if(field==='comment')snapshot.prompt_blocks[1].text=longText;
      if(field==='extra')snapshot.prompt_blocks.push({id:'refinement_extra',kind:'comment',label:'Additional refinement',text:'Round the outer edges without changing the bore.'});
      renderState(snapshot);
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const ids=['request_prompt',...snapshot.prompt_blocks.map(block=>block.id)];
      samples.push({field,cards:ids.map(id=>{const card=document.getElementById(id),content=card.querySelector('.content');return {width:card.offsetWidth,height:card.offsetHeight,text:content.textContent.trimEnd(),readOnly:!content.isContentEditable&&!card.querySelector('input,textarea,[contenteditable="true"]')};}),expected:[snapshot.prompt,...snapshot.prompt_blocks.map(block=>block.text)],position:[group.offsetLeft,group.offsetTop],originalPosition:position,hasResizeHandle:!!group.querySelector('.group-resize'),manualSize:group.classList.contains('user-sized'),attachment:group.textContent.includes('bracket-drawing.png')});
    }
    return samples;
  })()`);
  const shortHeight=result[0].cards[0].height;
  for(const sample of result){
    assert.deepEqual(sample.cards.map(card=>card.text),sample.expected,'Recorded inputs must survive rendering and subsequent updates');
    assert.equal(sample.attachment,true,'Attachments must remain available');
    assert.deepEqual(sample.position,sample.originalPosition,'Automatic card sizing must preserve the placed block position');
    assert.equal(sample.hasResizeHandle,true);assert.equal(sample.manualSize,true,'Manual dimensions must remain applied while source cards update');
    for(const card of sample.cards){
      assert.equal(card.readOnly,true,'Source text is observed, not manually edited in the graph');
      assert.equal(card.width,sample.cards[0].width);assert.equal(card.height,sample.cards[0].height,'Each source card must match the tallest input');
    }
    if(['prompt','spec','comment'].includes(sample.field))assert.ok(sample.cards[0].height>shortHeight,'Growing any source must grow all cards together');
  }
});

test('Usage counts deduplicate category matches and reset for a new route without marking declared MCP used', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const tools=config.nodes.filter(node=>node.kind==='tool').slice(0,2),bash=commandTemplateFor('bash');
    for(const tool of tools)tool.match={command_prefixes:['shared-fixture-cli']};
    viewMode='saved';viewRunId='usage-overlap';
    const initial={run_id:viewRunId,prompt:'Inspect fixture services',orchestrator_phase:'running',nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),edges:[],timestamp:'2026-01-01T00:00:00Z',event:{type:'PROMPT_START',prompt:'Inspect fixture services'}};
    const start=structuredClone(initial);start.nodes[bash.id]={...bash,status:'active'};start.timestamp='2026-01-01T00:00:01Z';start.event={type:'TOOL_CALL_START',tool:'bash',call_id:'shared-cli-call',input:JSON.stringify({command:'rtk proxy shared-fixture-cli --inspect'})};start.edges=[{from:'agent-hub',to:bash.id},...tools.map(tool=>({from:bash.id,to:tool.id}))];
    const end=structuredClone(start);end.timestamp='2026-01-01T00:00:02Z';end.event={type:'TOOL_CALL_END',tool:'bash',call_id:'shared-cli-call',success:true,result:'Captured service response'};
    steps=[initial,start,end];showStep(2);await frame();
    const used={toolCounts:tools.map(tool=>state.nodes[tool.id].usage_count),commandCount:state.nodes[bash.id].usage_count,category:document.querySelector('#tool-count').textContent,mcp:state.nodes.mcp_demo.usage_count,cards:tools.map(tool=>document.getElementById(tool.id).querySelector('.call-meta').textContent)};
    const next=structuredClone(initial);next.run_id='usage-next';steps=[next];viewRunId=next.run_id;showStep(0);await frame();
    return {used,reset:tools.map(tool=>({count:state.nodes[tool.id].usage_count,status:state.nodes[tool.id].status})),mcpReset:state.nodes.mcp_demo.usage_count};
  })()`);
  assert.deepEqual(result.used.toolCounts,[1,1]);
  assert.equal(result.used.commandCount,1,'Start and completion are one logical invocation');
  assert.match(result.used.category,/\b1 call\b/,'A category counts an invocation once even when two tools match');
  for(const text of result.used.cards)assert.match(text,/×1\b/,'Recorded counts must be visible on the resource cards');
  assert.equal(result.used.mcp,0,'Inventory alone must not count as MCP use');
  assert.deepEqual(result.reset,[{count:0,status:'idle'},{count:0,status:'idle'}]);
  assert.equal(result.mcpReset,0);
});

test('Invocation meters keep fifty squares, collapse on overflow or narrow width, and clear when rewound', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const samples=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    config.block_layouts={...config.block_layouts,tools:'list'};
    const tool=config.nodes.find(node=>node.id==='tool_0'),bash=commandTemplateFor('bash'),group=document.querySelector('.g-tools');
    tool.match={command_prefixes:['meter-fixture-cli']};group.style.width='480px';
    viewMode='saved';viewRunId='meter-boundaries';
    const base={run_id:viewRunId,prompt:'Inspect the fixture service repeatedly',orchestrator_phase:'running',nodes:Object.fromEntries([...config.nodes,bash].map(node=>[node.id,{...node,status:'idle'}])),edges:[{from:'agent-hub',to:bash.id},{from:bash.id,to:tool.id}],timestamp:'2026-01-01T00:00:00Z',event:{type:'PROMPT_START',prompt:'Inspect the fixture service repeatedly'}};
    steps=[base];
    for(let i=0;i<51;i++){
      steps.push({...base,timestamp:new Date(1767225601000+i*2000).toISOString(),event:{type:'TOOL_CALL_START',tool:'bash',call_id:'meter-'+i,input:JSON.stringify({command:'rtk proxy meter-fixture-cli --inspect'})}});
      steps.push({...base,timestamp:new Date(1767225602000+i*2000).toISOString(),event:{type:'TOOL_CALL_END',tool:'bash',call_id:'meter-'+i,success:true,result:'Recorded response'}});
    }
    const samples=[],sample=name=>{
      const card=document.getElementById(tool.id),meter=card.querySelector('.resource-usage-meter'),fill=meter?.querySelector('.resource-usage-fill'),label=meter?.querySelector('.resource-usage-label'),style=meter&&getComputedStyle(meter),labelVisible=label&&getComputedStyle(label).display!=='none',gap=style?parseFloat(style.getPropertyValue('--usage-gap')):0;
      const rect=meter?.getBoundingClientRect(),control=card.querySelector(':scope>.comment-widget')?.getBoundingClientRect(),centerDelta=rect&&control?Math.abs(rect.top+rect.height/2-control.top-control.height/2):0;
      samples.push({name,count:state.nodes[tool.id].usage_count,card:[card.offsetWidth,card.offsetHeight],meter:!!meter,labelVisible:!!labelVisible,label:label?.textContent,accessible:meter?.getAttribute('aria-label'),segments:meter&&!labelVisible?Math.round((fill.offsetWidth+gap)/(fill.offsetHeight+gap)):null,fillWidth:fill?.offsetWidth,meterWidth:meter?.clientWidth,centerDelta});
    };
    showStep(2);await frame();sample('one');
    showStep(100);await frame();sample('fifty');
    showStep(102);await frame();sample('fifty-one');
    const fill=document.getElementById(tool.id).querySelector('.resource-usage-fill'),deadline=performance.now()+3000;
    await new Promise((resolve,reject)=>{function check(){if(fill.getAnimations().some(animation=>animation.playState==='running'))return resolve();if(performance.now()>deadline)return reject(new Error('Overflow pulse did not start'));requestAnimationFrame(check)}check()});
    showStep(100);await frame();sample('rewind-fifty');
    group.style.width='220px';await frame();sample('narrow');
    group.style.width='480px';await frame();sample('wide-again');
    showStep(0);await frame();sample('zero');
    showStep(102);await frame();
    return samples;
  })()`);
  const byName=Object.fromEntries(samples.map(sample=>[sample.name,sample]));
  assert.equal(byName.one.segments,1);
  for(const name of ['fifty','rewind-fifty','wide-again']){
    assert.equal(byName[name].segments,50,`${name}: exactly fifty squares must fit`);
    assert.equal(byName[name].labelVisible,false);
    assert.ok(byName[name].fillWidth<=byName[name].meterWidth);
  }
  for(const name of ['fifty-one','narrow']){
    const sample=byName[name];assert.equal(sample.labelVisible,true);
    assert.equal(sample.label,`${sample.count}+ calls`);
    assert.equal(sample.fillWidth,sample.meterWidth,'Overflow must be one full-width band');
    assert.equal(sample.accessible,`${sample.count} recorded calls`);
  }
  for(const name of ['fifty','fifty-one','rewind-fifty','wide-again','zero'])assert.deepEqual(byName[name].card,byName.one.card,'The meter must not resize its card');
  for(const sample of samples)assert.ok(sample.centerDelta<.1,`${sample.name}: meter and footer controls must share a centerline`);
  assert.equal(byName.zero.meter,false,'Unused or rewound resources must not retain a stale meter');
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  const reduced=await evaluate("(()=>{const meter=document.querySelector('#tool_0 .resource-usage-meter');return {animations:meter.querySelector('.resource-usage-fill').getAnimations().length,label:meter.querySelector('.resource-usage-label').textContent}})()");
  assert.deepEqual(reduced,{animations:0,label:'51+ calls'});
});

test('Instrument meters avoid footer actions across commands, tools, MCP, skills, integrations and memory without changing card geometry', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))),bash=commandTemplateFor('bash'),read=commandTemplateFor('read');
    viewMode='saved';viewRunId='meter-footers';
    const base={run_id:viewRunId,prompt:'Inspect captured instrument usage',orchestrator_phase:'running',nodes:Object.fromEntries([...config.nodes,bash,read].map(node=>[node.id,{...node,status:'idle'}])),edges:[],timestamp:'2026-01-01T00:00:00Z',event:{type:'PROMPT_START',prompt:'Inspect captured instrument usage'}};
    steps=[base];
    const events=[
      {tool:'bash',input:JSON.stringify({command:'rtk proxy openscad --version'})},
      {tool:'mcp__demo__inspect',input:'{}',mcp_server:'demo'},
      {tool:'read',input:JSON.stringify({path:'skill://demo'})},
      {tool:'memory_recall',input:'{}',memory_system_id:'demo',memory_bank_id:'demo',memory_operation:'recall'}
    ];
    for(const [index,event]of events.entries()){
      steps.push({...base,timestamp:new Date(1767225601000+index*2000).toISOString(),event:{...event,type:'TOOL_CALL_START',call_id:'footer-'+index}});
      steps.push({...base,timestamp:new Date(1767225602000+index*2000).toISOString(),event:{...event,type:'TOOL_CALL_END',call_id:'footer-'+index,success:true,result:'Captured response'}});
    }
    showStep(steps.length-1);await frame();
    const cards=[bash.id,read.id,'tool_0','mcp_demo','skill_demo','integration_rtk','memory_demo'].map(id=>document.getElementById(id));
    const geometry=()=>cards.map(card=>[card.offsetLeft,card.offsetTop,card.offsetWidth,card.offsetHeight]),before=geometry();
    const overlap=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
    const meters=cards.map(card=>{
      const meter=card.querySelector('.resource-usage-meter'),rect=meter.getBoundingClientRect(),bounds=card.getBoundingClientRect();
      const controls=[...card.querySelectorAll(':scope>.comment-widget,:scope>.execution-details-button,:scope>.capability-pin-button')].map(control=>control.getBoundingClientRect());
      return {id:card.id,label:meter.getAttribute('aria-label'),inside:rect.left>=bounds.left&&rect.right<=bounds.right&&rect.top>=bounds.top&&rect.bottom<=bounds.bottom,overlaps:controls.some(control=>overlap(rect,control)),aligned:controls.every(control=>Math.abs(rect.top+rect.height/2-control.top-control.height/2)<.1)};
    });
    for(const card of cards)card.querySelector('.resource-usage-meter').remove();
    const without=geometry();resourceLayouts.update();await frame();
    return {meters,before,without,restored:geometry(),otherMeters:document.querySelectorAll('.g-docs .resource-usage-meter,.g-files .resource-usage-meter,.g-request .resource-usage-meter').length};
  })()`);
  assert.deepEqual(result.without,result.before);assert.deepEqual(result.restored,result.before);
  assert.equal(result.otherMeters,0,'Document, file and prompt layouts are not instrument meters');
  for(const meter of result.meters){assert.equal(meter.inside,true,meter.id);assert.equal(meter.overlaps,false,meter.id);assert.equal(meter.aligned,true,`${meter.id}: meter and buttons must share a centerline`);assert.equal(meter.label,'1 recorded call')}
});

function playbackClock() {
  let now=0,serial=0;const timers=new Map(),microtasks=[];
  const context={window:{},performance:{now:()=>now},queueMicrotask:fn=>microtasks.push(fn),setTimeout:(fn,delay)=>{const id=++serial;timers.set(id,{fn,at:now+delay});return id},clearTimeout:id=>timers.delete(id)};
  runInNewContext(readFileSync(new URL('route-playback.js',web),'utf8'),context);
  const flush=()=>{let count=0;while(microtasks.length){assert.ok(++count<100,'Playback callback reentry did not settle');microtasks.shift()()}};
  const advance=ms=>{const end=now+ms;let count=0;for(;;){const next=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>end)break;assert.ok(++count<100,'Playback timer loop did not settle');timers.delete(next[0]);now=next[1].at;next[1].fn();flush()}now=end;flush()};
  return {Playback:context.window.ObserverRoutePlayback,flush,advance};
}

test('Visual playback waits for arrival and dimming, preserves concurrent event order, and rejects stale completions', () => {
  const clock=playbackClock(),seen=[];
  const a={key:'a-start',index:0,kind:'call',phase:'input',callStep:0,atomic:false},b={...a,key:'b-start',index:1,callStep:1},endA={...a,key:'a-end',index:2,phase:'output'};
  let latest=[a],runKey='one',playback;
  playback=new clock.Playback({onChange:()=>{seen.push(playback.current?[playback.current.stage.key,playback.current.dimming]:null);playback.sync({runKey,stages:latest,live:true})}});
  playback.sync({runKey,stages:latest,live:true});clock.flush();
  latest=[a,b,endA];playback.sync({runKey,stages:latest,live:true});
  clock.advance(5000);assert.equal(playback.current.stage.key,a.key,'Pending input must not expire before arrival');
  playback.finish('unknown');clock.advance(1000);assert.equal(playback.current.stage.key,a.key);
  playback.finish(a.key);clock.advance(0);assert.equal(playback.current.dimming,true);
  clock.advance(179);assert.equal(playback.current.stage.key,a.key);
  clock.advance(1);assert.equal(playback.current.stage.key,b.key);
  playback.finish(a.key);clock.advance(1000);assert.equal(playback.current.stage.key,b.key,'Stale A arrival must not finish B');
  playback.finish(b.key);clock.advance(180);assert.equal(playback.current.stage.key,endA.key,'A completion must not skip B start');
  playback.finish(endA.key);clock.advance(99);assert.equal(playback.current.dimming,false);
  clock.advance(1);assert.equal(playback.current.dimming,true);clock.advance(180);
  assert.equal(playback.current,null);
  assert.deepEqual(seen,[[a.key,false],[a.key,true],[b.key,false],[b.key,true],[endA.key,false],[endA.key,true],null]);
  runKey='two';latest=[{...a,key:'next-run'}];playback.sync({runKey,stages:latest,live:true});clock.flush();
  playback.finish(a.key);clock.advance(1000);assert.equal(playback.current.stage.key,'next-run');
  playback.destroy();clock.advance(1000);assert.equal(playback.current,null);
});

test('Static selection remains pinned; returning live does not replay old input, and reduced motion drains new events', () => {
  const clock=playbackClock(),stage={key:'first',index:0,kind:'call',phase:'input',callStep:0,atomic:false},playback=new clock.Playback({onChange:()=>{}});
  playback.sync({runKey:'run',stages:[stage],live:false});clock.flush();playback.finish(stage.key);clock.advance(10000);
  assert.equal(playback.current.stage.key,stage.key);assert.equal(playback.current.animate,false);
  playback.sync({runKey:'run',stages:[stage],live:true});clock.flush();assert.equal(playback.current.animate,false);
  const end={...stage,key:'end',index:1,phase:'output'};
  playback.sync({runKey:'run',stages:[stage,end],live:true,motion:false});
  playback.finish(stage.key);clock.advance(0);assert.equal(playback.current.stage.key,end.key);assert.equal(playback.current.animate,false);
  playback.finish(end.key);clock.advance(0);assert.equal(playback.current,null);
  playback.sync({runKey:'empty',stages:[],live:true});clock.flush();playback.sync({runKey:'empty',stages:[],live:true});clock.flush();
  assert.equal(playback.current,null);
  playback.destroy();
});

test('Commands, services, MCP, skills, integrations, memory and result gain focus only after frame arrival, with a separate leaf marker', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))),read=commandTemplateFor('read');
    const cases=[
      {id:read.id,tool:'read',input:JSON.stringify({path:'fixtures/source.txt'})},
      {id:'tool_0',tool:'OpenSCAD CLI',input:'{}'},
      {id:'mcp_demo',tool:'mcp__demo__inspect',input:'{}',mcp_server:'demo'},
      {id:'skill_demo',tool:'read',input:JSON.stringify({path:'skill://demo'})},
      {id:'integration_rtk',tool:'bash',input:JSON.stringify({command:'rtk proxy echo observed'})},
      {id:'memory_demo',tool:'memory_recall',input:'{}',memory_system_id:'demo',memory_bank_id:'demo',memory_operation:'recall'},
      {id:'run_result',result:true}
    ],samples=[];
    for(const item of cases){
      viewMode='demo';viewRunId='arrival-'+item.id;selectedCallStep=null;setRouteScope('current');
      const nodes=Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}]));
      nodes[item.id]={...(nodes[item.id]||{id:item.id,kind:item.result?'result':'command',label:'Observed target'}),status:item.result?'hit':'active'};
      const event=item.result?{type:'GENERATION_END',success:true,result:'Observed result'}:{...item,type:'TOOL_CALL_START',call_id:viewRunId};
      steps=[{run_id:viewRunId,prompt:'Arrival fixture',orchestrator_phase:item.result?'complete':'running',nodes,edges:[{from:'agent-hub',to:item.id}],timestamp:'2026-01-01T00:00:00Z',event}];
      showStep(0);await frame();
      const card=document.getElementById(item.id),group=[...svg.querySelectorAll('g[data-edge]')].find(group=>wireData.get(group.dataset.edge)?.toId===item.id),entry=wireEntries.get(group);
      const before={focused:card.classList.contains('sequence-focus'),painted:card.classList.contains(item.result?'hit':'active'),mask:Boolean(group?.querySelector('mask'))};
      group.querySelector('.edge-hit').dispatchEvent(new PointerEvent('pointerenter'));
      before.hoverFocused=card.classList.contains('wire-endpoint');wireHovered=null;updateWireFocus();
      if(item.id===read.id){
        const holder=document.getElementById('commands-container');holder.style.display='grid';holder.style.gridTemplateColumns='repeat(3,minmax(0,1fr))';card.style.gridColumn='3';
        scale=.45;panX=90;panY=-30;setWorld();await frame();
      }
      const maskGeometry=group.querySelector('mask path')?.getAttribute('d'),geometry=group.querySelector('.edge').getAttribute('d');
      const duration=entry.animations[0].effect.getTiming().duration;
      const advance=async()=>{for(const animation of entry.animations)animation.finish();await frame();for(const animation of entry.animations)animation.pause()};
      await advance();const framePhase={phase:entry.phase,focused:card.classList.contains('sequence-focus')};
      const framePort=group.querySelector(group.dataset.direction==='return'?'.source-port':'.target-port'),frameY=Number(framePort.getAttribute('cy'));
      await advance();const leafPhase={phase:entry.phase,focused:card.classList.contains('sequence-focus')};
      await advance();const highlighted={phase:entry.phase,focused:card.classList.contains('sequence-focus'),delivered:wireSeen.has(visibleRouteContext().key+':'+group.dataset.edge)};
      entry?.finish(true);await frame();
      const dot=group.querySelector('.leaf-port[data-target="'+item.id+'"]'),bounds=elementBox(card),dotPoint=dot?{x:Number(dot.getAttribute('cx')),y:Number(dot.getAttribute('cy'))}:null;
      samples.push({id:item.id,before,duration,framePhase,leafPhase,highlighted,frameY,cy:bounds.cy,matchingMask:maskGeometry===geometry,after:{focused:card.classList.contains('sequence-focus'),painted:card.classList.contains(item.result?'hit':'active'),dot:Boolean(dot),dotOutside:dotPoint&&(dotPoint.x<=bounds.left||dotPoint.x>=bounds.right||dotPoint.y<=bounds.top||dotPoint.y>=bounds.bottom),dotY:dotPoint?.y,color:dot?getComputedStyle(dot).fill:null,stroke:getComputedStyle(group.querySelector('.edge')).stroke}});
    }return samples;
  })()`);
  for(const sample of result){
    assert.deepEqual(sample.before,{focused:false,painted:false,mask:true,hoverFocused:false},sample.id);
    assert.ok(sample.duration>=1400&&sample.duration<=3000,`${sample.id}: readable travel duration`);
    assert.deepEqual(sample.framePhase,{phase:'frame',focused:false},sample.id);
    assert.deepEqual(sample.leafPhase,{phase:'leaf',focused:false},sample.id);
    assert.deepEqual(sample.highlighted,{phase:'settle',focused:true,delivered:false},sample.id);
    assert.ok(Math.abs(sample.after.dotY-sample.cy)<.1,`${sample.id}: leaf port must align with card center`);
    assert.equal(sample.matchingMask,true,`${sample.id}: moving/zooming must not detach the reveal mask`);
    assert.equal(sample.after.focused,true,sample.id);assert.equal(sample.after.painted,true,sample.id);
    assert.equal(sample.after.dot,true,sample.id);assert.equal(sample.after.dotOutside,true,`${sample.id}: leaf dot must not cover card text`);
    assert.equal(sample.after.color,sample.after.stroke,sample.id);
  }
});

test('Project Prompt stays dashed at first frame, midflight and arrival; crossings do not add artificial bends', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    viewMode='demo';viewRunId='prompt-dash-fixture';selectedCallStep=null;setRouteScope('current');
    steps=[{run_id:viewRunId,prompt:'Observed request',orchestrator_phase:'running',nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),edges:[{from:'request_prompt',to:'agent-hub'}],timestamp:'2026-01-01T00:00:00Z',event:{type:'PROMPT_START',prompt:'Observed request'}}];
    showStep(0);await frame();
    const group=svg.querySelector('.request-wire'),path=group.querySelector('.edge'),entry=wireEntries.get(group),dash=()=>getComputedStyle(path).strokeDasharray;
    const first=dash(),before=document.getElementById('agent-hub').classList.contains('sequence-focus'),mask=Boolean(path.getAttribute('mask'));
    for(const animation of entry.animations){animation.pause();animation.currentTime=animation.effect.getTiming().duration/2}
    await frame();const middle=dash();entry.finish(true);await frame();const after=dash(),arrived=document.getElementById('agent-hub').classList.contains('sequence-focus');
    const straight=document.createElementNS('http://www.w3.org/2000/svg','path');straight.setAttribute('d',roundedRoute([{x:0,y:50},{x:100,y:50}],[{a:{x:50,y:0},b:{x:50,y:100}}]));svg.appendChild(straight);
    const deviations=[];for(let at=0;at<=straight.getTotalLength();at+=2)deviations.push(Math.abs(straight.getPointAtLength(at).y-50));const length=straight.getTotalLength();straight.remove();
    return {first,middle,after,before,arrived,mask,length,maxDeviation:Math.max(...deviations)};
  })()`);
  assert.ok(result.first.split(',').every(value=>parseFloat(value)>0),'Request paths must be dashed from their first painted frame');
  assert.equal(result.middle,result.first);assert.equal(result.after,result.first);
  assert.equal(result.mask,true);assert.equal(result.before,false);assert.equal(result.arrived,true);
  assert.equal(result.length,100);assert.equal(result.maxDeviation,0,'A straight route must stay straight at another route crossing');
});

test('Pending and consecutive calls retain one visible pulsing wire and its counter until completion', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))),read=commandTemplateFor('read');
    const settle=async()=>{const start=performance.now();for(;;){for(const entry of [...wireEntries.values()])entry.finish(true);await frame();if(!routePlayback.queue.length&&(!routePlayback.current||routePlayback.finished&&visibleRouteContext()?.phase==='input'))return;if(performance.now()-start>6000)throw Error('Route did not settle')}};
    viewMode='demo';viewRunId='persistent-call-fixture';selectedCallStep=null;setRouteScope('current');
    const snapshot={run_id:viewRunId,prompt:'Repeated read',orchestrator_phase:'running',nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),edges:[{from:'agent-hub',to:read.id}],timestamp:'2026-01-01T00:00:00Z',event:{type:'TOOL_CALL_START',tool:'read',call_id:'first',input:JSON.stringify({path:'fixtures/source.txt'})}};
    snapshot.nodes[read.id]={...read,status:'active'};steps=[snapshot];showStep(0);await frame();await settle();
    const group=svg.querySelector('g[data-edge="agent~commands"]'),path=group.querySelector('.edge');
    const inspect=()=>({same:group===svg.querySelector('g[data-edge="agent~commands"]')&&path===group.querySelector('.edge'),visible:parseFloat(getComputedStyle(path).opacity)>.1,masked:path.hasAttribute('mask')||group.classList.contains('edge-entering'),pulsing:getComputedStyle(path).animationName.includes('tool-route-pulse'),direction:group.dataset.direction,label:group.querySelector('.edge-step').textContent,labelVisible:getComputedStyle(group.querySelector('.edge-step')).display!=='none'});
    const append=async(event,phase='running')=>{const next=structuredClone(snapshot);next.event=event;next.orchestrator_phase=phase;next.timestamp='2026-01-01T00:00:0'+steps.length+'Z';steps.push(next);showStep(steps.length-1);await frame()};
    const pending=inspect();
    await append({type:'THINKING',phase:'Waiting for the tool'});await settle();const waiting=inspect();
    await append({...snapshot.event,call_id:'second'});await settle();const parallel=inspect();
    await append({type:'TOOL_CALL_END',tool:'read',call_id:'first',success:true,result:'First result'});await settle();const oneFinished=inspect();
    await append({type:'TOOL_CALL_END',tool:'read',call_id:'second',success:true,result:'Second result'});await settle();const completed=inspect();
    await append({...snapshot.event,call_id:'third'});await settle();const consecutive=inspect();
    await append({type:'GENERATION_END',result:'Interrupted'},'error');await settle();const interrupted=inspect();
    viewRunId='new-persistent-route';steps=[{...structuredClone(snapshot),run_id:viewRunId,edges:[],event:{type:'PROMPT_START',prompt:'Another run'}}];showStep(0);await frame();
    return{pending,waiting,parallel,oneFinished,completed,consecutive,interrupted,cleared:!svg.querySelector('g[data-edge="agent~commands"]')};
  })()`);
  for(const sample of [result.pending,result.waiting,result.parallel,result.oneFinished,result.consecutive]){
    assert.equal(sample.same,true);assert.equal(sample.visible,true);assert.equal(sample.masked,false);
    assert.equal(sample.pulsing,true);assert.equal(sample.labelVisible,true);
    assert.equal(sample.direction,'forward');
  }
  assert.match(result.parallel.label,/×2/);assert.match(result.oneFinished.label,/×2/);
  assert.match(result.consecutive.label,/×3/);
  for(const sample of [result.completed,result.interrupted]){
    assert.equal(sample.same,true);assert.equal(sample.visible,true);assert.equal(sample.masked,false);assert.equal(sample.pulsing,false);
  }
  assert.equal(result.cleared,true);
});
test('Scrolled command cards fade into directional shelves without stretching, hiding fragments or moving file routes', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    viewMode='saved';viewRunId='command-scroll-fade';selectedCallStep=null;
    const commands=['write','edit','bash','grep','glob','read'].map(name=>commandTemplateFor(name)),nodes=Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),edges=[];
    const fileId='working_file_'+encodeURIComponent('src/route-proof.py');
    nodes[fileId]={id:fileId,kind:'file',label:'src/route-proof.py',category:'Working file',status:'hit'};
    edges.push({from:'agent-hub',to:fileId,label:'explicit file argument'});
    steps=[];
    for(const command of commands){
      nodes[command.id]={...command,status:'active'};edges.push({from:'agent-hub',to:command.id});
      const base={run_id:viewRunId,prompt:'Used command scroll',orchestrator_phase:'running',nodes:structuredClone(nodes),edges:structuredClone(edges),timestamp:'2026-01-01T00:00:00Z'};
      steps.push({...base,event:{type:'TOOL_CALL_START',tool:command.label,call_id:command.id,input:{path:'src/route-proof.py'}}});
      nodes[command.id].status='hit';
      steps.push({...base,nodes:structuredClone(nodes),event:{type:'TOOL_CALL_END',tool:command.label,call_id:command.id,success:true,result:'Recorded result'}});
    }
    showStep(steps.length-1);await frame();
    const group=document.querySelector('.g-commands'),body=group.querySelector('.group-body');
    group.style.height='460px';body.style.maxHeight='none';body.scrollTop=0;renderEdges();await frame();
    const used=[...body.querySelectorAll('.card[id]')].filter(el=>commands.some(command=>command.id===el.id)).sort((a,b)=>elementBox(a).top-elementBox(b).top),first=used[0];
    const paths=()=>[...svg.querySelectorAll('g[data-edge]')].map(group=>[group.dataset.edge,group.querySelector('path.edge')?.getAttribute('d')]).sort((a,b)=>a[0].localeCompare(b[0]));
    const docks=()=>['top','bottom'].map(side=>{
      const bounds=elementBox(body),hidden=used.filter(el=>{const r=elementBox(el);return side==='top'?r.bottom<=bounds.top:r.top>=bounds.bottom});
      const dock=group.querySelector('.edge-pins-'+side),pins=dock&&!dock.hidden?[...dock.children]:[],individual=pins.filter(pin=>!pin.classList.contains('edge-pin-overflow')),overflow=pins.find(pin=>pin.classList.contains('edge-pin-overflow'));
      return {side,hidden:hidden.length,individual:individual.length,overflow:overflow?.textContent||'',represented:hidden.every(el=>pins.some(pin=>pin.dataset.pinKey===el.id||pin.title.split('\\n').includes(el.querySelector('.card-title').textContent))),correct:individual.every(pin=>hidden.some(el=>el.id===pin.dataset.pinKey))};
    });
    const atTop=docks(),beforePaths=paths();
    body.scrollTop+=elementBox(first).top-elementBox(body).top+(elementBox(first).bottom-elementBox(first).top)/2;renderEdges();await frame();
    const partial={visible:getComputedStyle(first).visibility!=='hidden',pinned:!!group.querySelector('.edge-pin[data-pin-key="'+first.id+'"]'),intersects:elementBox(first).bottom>elementBox(body).top,clipped:elementBox(first).top<elementBox(body).top};
    body.scrollTop+=elementBox(first).bottom-elementBox(body).top+8;renderEdges();await frame();
    const pin=group.querySelector('.edge-pin[data-pin-key="'+first.id+'"]'),animation=pin?.getAnimations()[0];
    if(!animation)throw Error('Newly hidden command did not fade in');
    animation.pause();animation.currentTime=70;await frame();
    const middle={height:elementBox(pin).bottom-elementBox(pin).top,transform:getComputedStyle(pin).transform,opacity:Number(getComputedStyle(pin).opacity),text:pin.querySelector('.edge-pin-label').textContent};
    animation.finish();await frame();
    const nearTop=docks();
    body.scrollTop=body.scrollHeight;renderEdges();await frame();
    const atBottom=docks(),afterPaths=paths(),bounds=elementBox(body);
    const holes=[...body.querySelectorAll('.edge-original-pinned')].filter(el=>{const r=elementBox(el);return r.bottom>bounds.top+.5&&r.top<bounds.bottom-.5}).map(el=>el.id);
    const dock=group.querySelector('.edge-pins:not([hidden])'),beforeWheel=body.scrollTop;
    dock.dispatchEvent(new WheelEvent('wheel',{deltaY:-80,bubbles:true,cancelable:true}));await frame();revealScroller(body);await frame();
    const track=overlayTracks.find(track=>track.dataset.axis==='y'),r=track.getBoundingClientRect(),title=group.querySelector('.group-title').getBoundingClientRect(),whole=group.getBoundingClientRect();
    const scroll={forwarded:body.scrollTop<beforeWheel,top:Math.abs(r.top-title.bottom-5)<1,bottom:Math.abs(r.bottom-(whole.bottom-11))<1,thumb:track.firstElementChild.getBoundingClientRect().width,hit:r.width};
    const labels=[...group.querySelectorAll('.edge-pin-calls')].map(el=>el.textContent);
    body.scrollTop=0;renderEdges();await frame();
    return {partial,middle,atTop,nearTop,atBottom,restoredTop:docks(),holes,labels,scroll,stable:JSON.stringify(beforePaths)===JSON.stringify(afterPaths),workingFiles:beforePaths.some(([key])=>key.includes('files')),restored:getComputedStyle(first).visibility!=='hidden'};
  })()`);
  assert.deepEqual(result.partial,{visible:true,pinned:false,intersects:true,clipped:true});
  assert.equal(result.middle.height,32);assert.equal(result.middle.transform,'none');assert.ok(result.middle.opacity>.65&&result.middle.opacity<1);assert.ok(result.middle.text);
  for(const shelves of [result.atTop,result.nearTop,result.atBottom,result.restoredTop])for(const shelf of shelves){
    assert.equal(shelf.correct,true);assert.equal(shelf.represented,true);assert.equal(shelf.individual,Math.min(2,shelf.hidden));
    if(shelf.hidden>2)assert.ok(shelf.overflow.includes((shelf.hidden-2)+' hidden'));else assert.equal(shelf.overflow,'');
  }
  assert.equal(result.atTop[0].hidden,0);assert.equal(result.atBottom[1].hidden,0);assert.equal(result.restoredTop[0].hidden,0);
  assert.equal(result.restored,true);assert.deepEqual(result.holes,[]);assert.ok(result.labels.every(label=>/#\d/.test(label)&&label.includes('×1')));
  assert.equal(result.workingFiles,true);assert.equal(result.stable,true);
  assert.deepEqual(result.scroll,{forwarded:true,top:true,bottom:true,thumb:4,hit:8});
});


test('Historical paths leave visible underpass gaps at crossings and do not darken shared trunks', async () => {
  await preparePage();
  const samples=await evaluate(`(async()=>{
    const ns='http://www.w3.org/2000/svg',samples=[];
    for(const other of [{a:{x:60,y:10},b:{x:60,y:110}},{a:{x:50,y:60},b:{x:90,y:60}}]){
      const group=document.createElementNS(ns,'g'),path=document.createElementNS(ns,'path'),points=[{x:10,y:60},{x:110,y:60}];
      path.setAttribute('d',roundedRoute(points));path.setAttribute('fill','none');path.setAttribute('stroke','#16816b');path.setAttribute('stroke-width','2');path.setAttribute('opacity','.04');group.appendChild(path);svg.appendChild(group);
      setWireCrossingGaps(group,points,[other]);
      const imageRoot=document.createElementNS(ns,'svg');imageRoot.setAttribute('xmlns',ns);imageRoot.setAttribute('width',120);imageRoot.setAttribute('height',120);imageRoot.setAttribute('viewBox','0 0 120 120');imageRoot.appendChild(group.cloneNode(true));
      const image=new Image();image.src='data:image/svg+xml;base64,'+btoa(new XMLSerializer().serializeToString(imageRoot));await image.decode();
      const canvas=document.createElement('canvas');canvas.width=canvas.height=120;const context=canvas.getContext('2d');context.drawImage(image,0,0);
      samples.push({cross:context.getImageData(60,60,1,1).data[3],uncut:context.getImageData(25,60,1,1).data[3]});group.remove();
    }return samples;
  })()`);
  for(const sample of samples){assert.equal(sample.cross,0,'Lower stroke must have a real gap, not a blended crossing');assert.ok(sample.uncut>=8,'Uncrossed history must remain faintly visible')}
});

test('Bubble Docs omit connector dots without losing delivery; List, callers and mid-handoff switching retain correct feedback', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  await evaluate(`(${documentReadFixture.toString()})(false,true)`);
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const findWire=()=>[...svg.querySelectorAll('g[data-edge]')].find(group=>wireData.get(group.dataset.edge)?.documentIds?.has('document_0'));
    const visible=element=>Boolean(element)&&getComputedStyle(element).display!=='none'&&getComputedStyle(element).visibility!=='hidden';
    const inspect=()=>{
      const group=findWire(),data=wireData.get(group.dataset.edge),receiver=group.querySelector(group.dataset.direction==='return'?'.source-port':'.target-port'),sender=group.querySelector(group.dataset.direction==='return'?'.target-port':'.source-port'),incoming=data.toId.startsWith('documents-');
      return {documentDot:visible(incoming?receiver:sender),callerDot:visible(incoming?sender:receiver),extraBubbleDots:group.querySelectorAll('.leaf-port[data-target="document_0"]').length,callerLeaf:visible(group.querySelector('.leaf-port[data-target="'+commandTemplateFor('read').id+'"]')),returning:data.returning,callId:data.call.ev.call_id};
    };
    const waitForBubble=()=>new Promise((resolve,reject)=>{const start=performance.now();function check(){if(arrivedRouteTargets(visibleRouteContext()).has('document_0'))resolve();else if(performance.now()-start>3000)reject(new Error('Bubble delivery was lost'));else requestAnimationFrame(check)}check()});
    for(const entry of [...wireEntries.values()])entry.finish(true);await frame();
    const bubble=inspect(),group=findWire(),entry=wireEntries.get(group);
    entry.animations[0].finish();await frame();await waitForBubble();
    const direct=entry.phase,bubbleFocused=documentBubbleView.getElement('document_0').classList.contains('sequence-focus');
    applyDocumentsView('list');await frame();const list=inspect();
    applyDocumentsView('bubble');await frame();
    const end=structuredClone(steps[0]);end.timestamp='2026-01-01T00:00:02Z';end.event={type:'TOOL_CALL_END',tool:'read',call_id:'fixture-read',success:true,result:'Observed document'};end.nodes.document_0.status='hit';end.nodes[commandTemplateFor('read').id].status='hit';steps.push(end);showStep(1,0);await frame();
    const returned=inspect();
    const fresh=structuredClone(steps[0]);fresh.run_id='document-flow-switch';viewRunId=fresh.run_id;viewMode='demo';selectedCallStep=null;applyDocumentsView('list');steps=[fresh];showStep(0);await frame();
    for(const active of [...wireEntries.values()])active.finish(true);await frame();
    const switching=wireEntries.get(findWire());switching.animations[0].finish();await frame();for(const animation of switching.animations)animation.pause();
    const beforeSwitch=switching.phase;applyDocumentsView('bubble');await frame();await waitForBubble();
    return {bubble,direct,bubbleFocused,list,returned,beforeSwitch,switched:{...inspect(),phase:switching.phase,focused:documentBubbleView.getElement('document_0').classList.contains('sequence-focus')},usage:state.nodes.document_0.usage_count};
  })()`);
  assert.equal(result.bubble.documentDot,false);assert.equal(result.bubble.extraBubbleDots,0);assert.equal(result.bubble.callerDot,true);
  assert.equal(result.bubble.callId,'fixture-read');assert.equal(result.direct,'arrived');assert.equal(result.bubbleFocused,true);
  assert.equal(result.list.documentDot,true);assert.equal(result.list.extraBubbleDots,1);assert.equal(result.list.callerDot,true);
  assert.equal(result.returned.returning,true);assert.equal(result.returned.documentDot,false);assert.equal(result.returned.extraBubbleDots,0);assert.equal(result.returned.callerDot,true);assert.equal(result.returned.callerLeaf,true);
  assert.equal(result.beforeSwitch,'frame');assert.equal(result.switched.phase,'arrived');assert.equal(result.switched.focused,true);assert.equal(result.switched.documentDot,false);assert.equal(result.switched.extraBubbleDots,0);assert.equal(result.usage,1);
});

test('Terminal exit planes prevent source and receiver hairpins in both vertical directions without crossing real obstacles', async () => {
  await preparePage();
  const samples=await evaluate(`(()=>{
    const samples=[];
    for(const mirrored of [false,true])for(const reversed of [false,true]){
      const make=(id,left,top,right,bottom)=>{const el=document.createElement('section');el.dataset.group=id;return{el,left,right,top:mirrored?1200-bottom:top,bottom:mirrored?1200-top:bottom}};
      const originalSource=make('source',100,100,300,600),originalTarget=make('target',400,800,600,1100),groups=[originalSource,originalTarget,make('below',340,350,700,700),make('left',4,200,80,400)],obstacles=groups.map(group=>({group:group.el,left:group.left-12,top:group.top-12,right:group.right+12,bottom:group.bottom+12})),y=value=>mirrored?1200-value:value;
      const source=reversed?originalTarget:originalSource,target=reversed?originalSource:originalTarget,from={cx:reversed?500:200,cy:y(reversed?900:300)},to={cx:reversed?200:500,cy:y(reversed?300:900)},reserved=[{a:{x:330,y:y(308)},b:{x:700,y:y(308)},active:true,key:'nearby-route',joins:[]}];
      const route=chooseGroupRoute(source,target,from,to,obstacles,12,reserved,{active:true});
      if(!route)throw new Error('Valid terminal route was dropped');
      const wraps=(owner,side,lane)=>wireSegments(route.spine).some(segment=>wireHitsRect(segment.a,segment.b,{left:side==='w'?lane.x:owner.left,right:side==='e'?lane.x:owner.right,top:owner.top,bottom:owner.bottom}));
      samples.push({mirrored,reversed,sourceWrap:wraps(source,route.sourceSide,route.spine[0]),targetWrap:wraps(target,route.targetSide,route.spine.at(-1)),hitsBlock:wireSegments(route.points).some(segment=>groups.some(group=>wireHitsRect(segment.a,segment.b,group))),sourceY:route.points[0].y,targetY:route.points.at(-1).y,expectedSourceY:from.cy,expectedTargetY:to.cy});
    }return samples;
  })()`);
  for(const sample of samples){
    const label=JSON.stringify({mirrored:sample.mirrored,reversed:sample.reversed});
    assert.equal(sample.sourceWrap,false,label);assert.equal(sample.targetWrap,false,label);assert.equal(sample.hitsBlock,false,label);
    assert.equal(sample.sourceY,sample.expectedSourceY,label);assert.equal(sample.targetY,sample.expectedTargetY,label);
  }
});

async function nativeKey(key, code, windowsVirtualKeyCode) {
  await command('Input.dispatchKeyEvent',{type:'rawKeyDown',key,code,windowsVirtualKeyCode});
  await command('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode});
}
async function nativePointer(type, point, extra={}) {
  await command('Input.dispatchMouseEvent',{type,x:point.x,y:point.y,...extra});
}
const nextFrames = "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))";

test('Canvas pans retain their reached position after release, capture loss, cancellation and window blur at all zoom limits', async () => {
  await preparePage();
  const original=await evaluate("({scale,panX,panY})");
  try {
    for (const zoom of [.15,.5,1,1.8]) for (const ending of ['release','capture','cancel','blur']) {
      const start=await evaluate(`(()=>{setPanMode(false);scale=${zoom};panX=viewport.clientWidth+100;panY=viewport.clientHeight+100;setWorld();const r=viewport.getBoundingClientRect();return{x:r.left+400,y:r.top+250,panX,panY,positions:JSON.stringify(groupPositions)}})()`);
      const end={x:start.x+80,y:start.y+40};
      await nativePointer('mouseMoved',start);
      await nativePointer('mousePressed',start,{button:'left',clickCount:1});
      await nativePointer('mouseMoved',end,{button:'left',buttons:1});
      if(ending==='capture')await evaluate("viewport.releasePointerCapture(drag.id)");
      else if(ending==='cancel')await evaluate("viewport.dispatchEvent(new PointerEvent('pointercancel',{pointerId:drag.id,bubbles:true}))");
      else if(ending==='blur')await evaluate("window.dispatchEvent(new Event('blur'))");
      await nativePointer('mouseReleased',end,{button:'left',clickCount:1});
      await evaluate(nextFrames);
      const result=await evaluate("({panX,panY,dragging:!!drag,positions:JSON.stringify(groupPositions),scroll:[viewport.scrollLeft,viewport.scrollTop]})");
      assert.ok(Math.abs(result.panX-start.panX-80)<.1,`${zoom}/${ending}: horizontal camera snapback`);
      assert.ok(Math.abs(result.panY-start.panY-40)<.1,`${zoom}/${ending}: vertical camera snapback`);
      assert.equal(result.dragging,false);assert.equal(result.positions,start.positions);assert.deepEqual(result.scroll,[0,0]);
    }
  } finally {
    await evaluate(`scale=${original.scale};panX=${original.panX};panY=${original.panY};setWorld();writeLocal('cad-map-camera',{scale,x:panX,y:panY})`);
  }
});

test('Hand mode pans over headers and cards rather than moving blocks; pointer-centered zoom retains its scene anchor', {timeout:15000}, async () => {
  await preparePage();
  for(const zoom of [.15,.5,1,1.8]){
    const setup=await evaluate(`(()=>{scale=${zoom};const group=document.querySelector('.g-commands');panX=300-group.offsetLeft*scale;panY=100-group.offsetTop*scale;setWorld();setPanMode(true);const r=group.querySelector('.group-title').getBoundingClientRect();return{x:r.left+Math.min(30,r.width/3),y:r.top+r.height/2,panX,panY,positions:JSON.stringify(groupPositions)}})()`);
    const end={x:setup.x+60,y:setup.y+40};
    await nativePointer('mousePressed',setup,{button:'left',clickCount:1});
    await nativePointer('mouseMoved',end,{button:'left',buttons:1});
    await nativePointer('mouseReleased',end,{button:'left',clickCount:1});
    const panned=await evaluate("({panX,panY,positions:JSON.stringify(groupPositions)})");
    assert.equal(panned.positions,setup.positions,`${zoom}: Hand mode moved a block`);
    assert.ok(Math.abs(panned.panX-setup.panX-60)<.1);assert.ok(Math.abs(panned.panY-setup.panY-40)<.1);
    const point=await evaluate("(()=>{const r=document.querySelector('#commands-container .card').getBoundingClientRect();return{x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}})()");
    await nativePointer('mouseWheel',point,{deltaX:45,deltaY:65});
    await evaluate(nextFrames);
    const wheel=await evaluate("({scale,panX,panY})");
    assert.ok(Math.abs(wheel.panX-panned.panX+45)<.1);assert.ok(Math.abs(wheel.panY-panned.panY+65)<.1);assert.equal(wheel.scale,zoom);
    const anchor=await evaluate(`({x:(${point.x}-viewport.getBoundingClientRect().left-panX)/scale,y:(${point.y}-viewport.getBoundingClientRect().top-panY)/scale})`);
    await nativePointer('mouseWheel',point,{deltaX:0,deltaY:-30,modifiers:2});
    await evaluate(nextFrames);
    const after=await evaluate(`({scale,x:(${point.x}-viewport.getBoundingClientRect().left-panX)/scale,y:(${point.y}-viewport.getBoundingClientRect().top-panY)/scale})`);
    assert.ok(after.scale>=.15&&after.scale<=1.8);assert.ok(Math.abs(after.x-anchor.x)<.01,`${zoom}: ${JSON.stringify({anchor,after})}`);assert.ok(Math.abs(after.y-anchor.y)<.01,`${zoom}: ${JSON.stringify({anchor,after})}`);
  }
  await evaluate("setPanMode(false)");
});

test('Catalog refresh preserves the camera and every block settings dialog isolates canvas shortcuts and closes with native Escape', {timeout:15000}, async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{setPanMode(false);scale=.67;panX=221;panY=-83;setWorld();const before={scale,panX,panY};removeLocal('memory-block-introduced');renderCatalog(false);await ${nextFrames};return{before,after:{scale,panX,panY}}})()`);
  assert.deepEqual(result.after,result.before,'Introducing Memory must not queue a camera override');
  const rapid=await evaluate("(async()=>{await blockSettings.open('commands',document.querySelector('.g-commands .group-settings-button'));blockSettings.close();await blockSettings.open('tools',document.querySelector('.g-tools .group-settings-button'));const result={busy:blockSettings.busy,open:blockSettings.dialog.open};blockSettings.close();return result})()");
  assert.deepEqual(rapid,{busy:false,open:true},'A delayed close event invalidated the newly opened settings request');
  for(const group of ['request','commands','tools','documents','files','capabilities','memory']){
    await evaluate(`document.querySelector('[data-group="${group}"] .group-settings-button').click()`);
    await evaluate("new Promise((resolve,reject)=>{const start=performance.now();const check=()=>!blockSettings.busy?resolve():performance.now()-start>2500?reject(new Error('Settings load did not settle: '+JSON.stringify({generation:blockSettings.generation,open:blockSettings.dialog.open,message:blockSettings.message.textContent}))):setTimeout(check,10);check()})");
    for(const [key,code,value] of [['0','Digit0',48],['1','Digit1',49],['+','Equal',187],['-','Minus',189],['h','KeyH',72],['v','KeyV',86]])await nativeKey(key,code,value);
    const modal=await evaluate("({camera:{scale,panX,panY},panMode,open:blockSettings.dialog.open})");
    assert.equal(modal.open,true,group);assert.equal(modal.panMode,false,group);assert.deepEqual(modal.camera,result.before,`${group}: modal shortcuts changed the camera`);
    await nativeKey('Escape','Escape',27);
    assert.equal(await evaluate("blockSettings.dialog.open"),false,`${group}: Escape did not close settings`);
  }
});

async function placeEdgeBubble(zoom,edge='left'){
  return evaluate(`(async()=>{
    setPanMode(false);applyDocumentsView('bubble');const view=documentBubbleView;view.stop();view.clearActivity();view.autoFit=false;
    const node=view.nodes.get('document_0');node.x=node.y=200;node.pinned=true;view.camera.scale=1;
    for(const other of view.nodes.values())if(other!==node){other.x=3000+other.index*80;other.y=3000}
    const x=${JSON.stringify(edge)}==='left'?8:${JSON.stringify(edge)}==='right'?view.width-8:view.width/2;
    const y=${JSON.stringify(edge)}==='top'?8:${JSON.stringify(edge)}==='bottom'?view.height-56:(view.height-48)/2;
    view.camera.x=x-node.x;view.camera.y=y-node.y;view.updateCamera();view.render();document.querySelector('.g-docs').style.zIndex='50';
    scale=${zoom};panX=panY=0;setWorld();let r=node.el.getBoundingClientRect(),v=viewport.getBoundingClientRect();panX=200-(r.left+r.width/2-v.left);panY=200-(r.top+r.height/2-v.top);setWorld();
    await ${nextFrames};r=node.el.getBoundingClientRect();
    const cx=r.left+r.width/2,cy=r.top+r.height/2,points=[{x:Math.round(cx),y:Math.round(cy)}];
    for(let y=Math.ceil(r.top);y<r.bottom;y++)for(let x=Math.ceil(r.left);x<r.right;x++)points.push({x,y});
    const point=points.find(p=>document.elementFromPoint(p.x,p.y)?.closest('.doc-bubble')===node.el);
    if(!point)throw new Error('No visible bubble pixel at '+${zoom}+'/'+${JSON.stringify(edge)});
    return{...point,local:{...view.camera},node:{x:node.x,y:node.y}}
  })()`);
}

test('Pressing and dragging a clipped bubble never recenters its camera, at every boundary and global zoom limit', async () => {
  await preparePage();const backup=await evaluate("documentBubbleView.options.loadPositions()");
  try{
    for(const zoom of [.15,.5,1,1.8])for(const edge of ['left','right','top','bottom']){
      const setup=await placeEdgeBubble(zoom,edge);
      await nativePointer('mouseMoved',setup);
      await nativePointer('mousePressed',setup,{button:'left',clickCount:1});
      const pressed=await evaluate("({camera:{...documentBubbleView.camera},drag:documentBubbleView.drag?.node.id,outer:!!drag})");
      assert.deepEqual(pressed.camera,setup.local,`${zoom}/${edge}: pointer focus recentered the map`);
      assert.equal(pressed.drag,'document_0',`${zoom}/${edge}: clipped bubble cannot be grabbed`);assert.equal(pressed.outer,false);
      const end={x:setup.x+8,y:setup.y+6};
      await nativePointer('mouseMoved',end,{button:'left',buttons:1});
      await nativePointer('mouseReleased',end,{button:'left',clickCount:1});
      const result=await evaluate("({x:documentBubbleView.nodes.get('document_0').x,y:documentBubbleView.nodes.get('document_0').y,drag:!!documentBubbleView.drag})");
      assert.ok(Math.abs(result.x-setup.node.x-8/zoom)<.1,`${zoom}/${edge}: horizontal drag scale`);
      assert.ok(Math.abs(result.y-setup.node.y-6/zoom)<.1,`${zoom}/${edge}: vertical drag scale`);assert.equal(result.drag,false);
    }
  }finally{await evaluate(`documentBubbleView.options.savePositions(${JSON.stringify(backup)})`)}
});

test('Bubble gesture ownership survives capture loss, removal, hiding and blur without blocking later wheel zoom', async () => {
  await preparePage();const backup=await evaluate("documentBubbleView.options.loadPositions()");
  try{
    for(const ending of ['capture','removal','hidden','blur']){
      const setup=await placeEdgeBubble(1);
      await nativePointer('mousePressed',setup,{button:'left',clickCount:1});
      await nativePointer('mouseMoved',{x:setup.x+1,y:setup.y},{button:'left',buttons:1});
      const owned=await evaluate("documentBubbleView.drag.pointer");
      const second=await evaluate(`(()=>{const view=documentBubbleView,other=view.nodes.get('document_1');other.el.dispatchEvent(new PointerEvent('pointerdown',{button:0,pointerId:99,pointerType:'touch',bubbles:true}));view.host.dispatchEvent(new PointerEvent('pointerdown',{button:0,pointerId:100,pointerType:'touch',bubbles:true}));return{owner:view.drag?.pointer,panning:!!view.pan}})()`);
      assert.equal(second.owner,owned);assert.equal(second.panning,false);
      if(ending==='capture'){
        await evaluate("documentBubbleView.drag.node.el.releasePointerCapture(documentBubbleView.drag.pointer)");
        await nativePointer('mouseMoved',{x:setup.x+2,y:setup.y},{button:'left',buttons:1});
      }
      else if(ending==='removal')await evaluate("documentBubbleView.setData(config.nodes.filter(node=>node.kind==='document'&&node.id!=='document_0'),config.document_links)");
      else if(ending==='hidden')await evaluate("applyDocumentsView('list')");
      else await evaluate("window.dispatchEvent(new Event('blur'))");
      await evaluate(nextFrames);
      const finished=await evaluate("({drag:!!documentBubbleView.drag,pan:!!documentBubbleView.pan,class:documentBubbleView.host.classList.contains('is-panning')})");
      assert.deepEqual(finished,{drag:false,pan:false,class:false},ending);
      await nativePointer('mouseReleased',{x:setup.x+40,y:setup.y+40},{button:'left',clickCount:1});
      await evaluate("if(resourceDialog?.open)closeResourceDetails();documentBubbleView.setData(config.nodes.filter(node=>node.kind==='document'),config.document_links);applyDocumentsView('bubble')");
      const point=await evaluate("(()=>{const view=documentBubbleView;view.stop();view.autoFit=false;view.camera.x=view.camera.y=10000;view.camera.scale=1;view.updateCamera();const r=view.host.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2}})()");
      await nativePointer('mouseWheel',point,{deltaX:0,deltaY:-40});
      await evaluate(nextFrames);
      assert.ok(await evaluate("documentBubbleView.camera.scale>1"),`${ending}: stale gesture blocked wheel zoom`);
      await nativePointer('mousePressed',point,{button:'left',clickCount:1});
      await nativePointer('mouseMoved',{x:point.x+20,y:point.y+10},{button:'left',buttons:1});
      assert.equal(await evaluate("!!documentBubbleView.pan"),true,`${ending}: background pan did not start`);
      await evaluate("applyDocumentsView('list');applyDocumentsView('bubble')");
      assert.equal(await evaluate("!!documentBubbleView.pan"),false);
      await nativePointer('mouseReleased',point,{button:'left',clickCount:1});
    }
  }finally{await evaluate(`documentBubbleView.options.savePositions(${JSON.stringify(backup)})`)}
});

test('Document tree retains a visible keyboard entry after hidden refresh, filtering and collapse, and preserves focused identity on rebuild', async () => {
  await preparePage();
  const result=await evaluate(`(()=>{
    const view=documentFilesView,docs=config.nodes.filter(n=>n.kind==='document'),samples=[];
    const sample=()=>{const visible=view.getVisibleItems(),zeros=[...view.treeContainer.querySelectorAll('[role="treeitem"][tabindex="0"]')];return{count:zeros.length,visible:zeros.every(item=>visible.includes(item))}};
    applyDocumentsView('bubble');view.setData(docs);applyDocumentsView('list');samples.push(sample());
    view.expandAll();view.focusItem(view.getElement('document_4'));view.searchQuery='reference/manuals';view.applySearchFilter();samples.push(sample());
    view.searchQuery='';view.applySearchFilter();view.expandAll();view.focusItem(view.getElement('document_4'));view.collapseAll();samples.push(sample());
    view.expandAll();view.focusItem(view.getElement('document_4'));view.setData(docs);samples.push(sample());
    return{samples,focused:document.activeElement?.dataset.nodeId};
  })()`);
  for(const sample of result.samples)assert.deepEqual(sample,{count:1,visible:true});
  assert.equal(result.focused,'document_4','Catalog rebuild discarded keyboard focus');
});

test('Activity keyboard selection can move repeatedly to earlier calls and shows the selected recorded output', async () => {
  await preparePage();await evaluate(`(${documentReadFixture.toString()})(false)`);
  await evaluate(`(async()=>{
    const base=structuredClone(steps[0]);steps=[];
    for(let i=0;i<3;i++)for(const end of [false,true]){const snapshot=structuredClone(base);snapshot.timestamp=new Date(Date.parse(base.timestamp)+steps.length*1000).toISOString();snapshot.event={type:end?'TOOL_CALL_END':'TOOL_CALL_START',tool:'read',call_id:'keyboard-'+i,input:JSON.stringify({path:'docs/standards/guide.md'}),...(end?{success:true,result:'captured-output-'+i}:{})};steps.push(snapshot)}
    showStep(steps.length-1);await ${nextFrames};openGroupResourceDetails('commands');document.querySelector('.activity-call-item.selected').focus();
  })()`);
  for(const ordinal of [2,1]){
    await nativeKey('ArrowUp','ArrowUp',38);
    const result=await evaluate("({ordinal:document.querySelector('.activity-call-item.selected .activity-call-item-num').textContent,focused:document.activeElement?.classList.contains('activity-call-item'),output:document.querySelector('.activity-detail-body').textContent})");
    assert.equal(result.ordinal,'#'+ordinal);assert.equal(result.focused,true);assert.ok(result.output.includes('captured-output-'+(ordinal-1)));
  }
  await evaluate("closeResourceDetails()");
});

test('Hidden graph and camera gestures pause travel without replay; enabling reduced motion cancels existing completion glows', async () => {
  await preparePage();await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  await evaluate(`(${documentReadFixture.toString()})(false,true)`);
  const paused=await evaluate(`(async()=>{
    switchTab('history');await ${nextFrames};const group=[...wireEntries.keys()][0],entry=wireEntries.get(group),before=entry.animations[0].currentTime,key=visibleRouteContext().key;
    zoomAt(scale*.95);await new Promise(r=>setTimeout(r,350));
    const after=entry.animations[0].currentTime,stopped=entry.animations[0].playState,stillKey=visibleRouteContext().key;
    switchTab('graph');await ${nextFrames};
    return{before,after,stopped,sameStage:key===stillKey,sameEntry:wireEntries.get(group)===entry,resumed:entry.animations[0].currentTime>after};
  })()`);
  assert.ok(Math.abs(paused.after-paused.before)<1);assert.equal(paused.stopped,'paused');assert.equal(paused.sameStage,true);assert.equal(paused.sameEntry,true);assert.equal(paused.resumed,true);
  await evaluate("completionGlow(document.getElementById('agent-hub'))");
  assert.ok(await evaluate("completionAnimations.size>0"));
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await evaluate(nextFrames);
  assert.equal(await evaluate("completionAnimations.size"),0);
  assert.equal(await evaluate("document.getElementById('agent-hub').hasAttribute('data-completion')"),false);
});

test('Playback pause preserves remaining hold and dim time while retaining queued event order', () => {
  const clock=playbackClock(),playback=new clock.Playback({onChange:()=>{}});
  const a={key:'a',index:0,kind:'call',phase:'output',callStep:0,atomic:true},b={key:'b',index:1,kind:'call',phase:'input',callStep:1,atomic:false};
  playback.sync({runKey:'pause',stages:[a],live:true});clock.flush();playback.finish('a');clock.advance(40);
  playback.setPaused(true);clock.advance(1000);assert.equal(playback.current.stage.key,'a');assert.equal(playback.current.dimming,false);
  playback.setPaused(false);clock.advance(59);assert.equal(playback.current.dimming,false);clock.advance(1);assert.equal(playback.current.dimming,true);
  clock.advance(50);playback.setPaused(true);playback.sync({runKey:'pause',stages:[a,b],live:true});clock.advance(1000);assert.equal(playback.current.stage.key,'a');
  playback.setPaused(false);clock.advance(129);assert.equal(playback.current.stage.key,'a');clock.advance(1);assert.equal(playback.current.stage.key,'b');
  playback.finish('a');clock.advance(1000);assert.equal(playback.current.stage.key,'b');
  playback.setPaused(true);playback.destroy();playback.setPaused(false);clock.advance(1000);assert.equal(playback.current,null);
});

test('An owned bubble gesture rejects camera controls, keyboard nudges and secondary resizing, then ends cleanly on a real host resize', {timeout:15000}, async () => {
  await preparePage();const backup=await evaluate("documentBubbleView.options.loadPositions()");
  try{
    const setup=await placeEdgeBubble(1);
    await nativePointer('mousePressed',setup,{button:'left',clickCount:1});
    await nativePointer('mouseMoved',{x:setup.x+6,y:setup.y+5},{button:'left',buttons:1});
    const before=await evaluate("({local:{...documentBubbleView.camera},outer:{scale,panX,panY},node:{x:documentBubbleView.nodes.get('document_0').x,y:documentBubbleView.nodes.get('document_0').y}})");
    for(const [key,code,value] of [['+','Equal',187],['0','Digit0',48],['ArrowRight','ArrowRight',39]])await nativeKey(key,code,value);
    await evaluate(`documentBubbleView.controls.children[2].click();documentBubbleView.controls.children[1].click();document.querySelector('#zoom-in').click();document.querySelector('.g-docs .group-resize[data-edge="se"]').dispatchEvent(new PointerEvent('pointerdown',{button:0,pointerId:99,pointerType:'touch',bubbles:true}))`);
    const after=await evaluate("({local:{...documentBubbleView.camera},outer:{scale,panX,panY},node:{x:documentBubbleView.nodes.get('document_0').x,y:documentBubbleView.nodes.get('document_0').y}})");
    assert.deepEqual(after,before,'A second interaction changed the gesture coordinate baseline');assert.equal(await evaluate("!!drag"),false);
    await nativePointer('mouseMoved',{x:setup.x+10,y:setup.y+8},{button:'left',buttons:1});
    const moved=await evaluate("({x:documentBubbleView.nodes.get('document_0').x,y:documentBubbleView.nodes.get('document_0').y})");
    assert.ok(Math.abs(moved.x-setup.node.x-10)<.1);assert.ok(Math.abs(moved.y-setup.node.y-8)<.1);
    const resized=await evaluate("(()=>{const group=document.querySelector('.g-docs'),height=group.style.height;group.style.height=group.offsetHeight+80+'px';documentBubbleView.resize();const result={local:!!documentBubbleView.drag,outer:!!drag};group.style.height=height;documentBubbleView.resize();return result})()");
    assert.deepEqual(resized,{local:false,outer:false});
    await nativePointer('mouseReleased',{x:setup.x+30,y:setup.y+20},{button:'left',clickCount:1});
  }finally{await evaluate(`documentBubbleView.options.savePositions(${JSON.stringify(backup)})`)}
});

test('Marquee multi-block dragging, Escape cancellation and corner resizing remain consistent at minimum, intermediate and maximum zoom', {timeout:20000}, async () => {
  await preparePage();
  const backup=await evaluate("({positions:structuredClone(groupPositions),sizes:structuredClone(groupSizes),manual:[...manuallyPlaced],camera:{scale,panX,panY},mode:documentViewMode,inspector:document.body.classList.contains('inspector-hidden'),sidebar:document.body.classList.contains('sessions-hidden'),storage:Object.fromEntries(Object.entries(localStorage))})");
  try{
    await evaluate("setSessionSidebar(true)");
    for(const zoom of [.15,.5,1,1.8]){
      const setup=await evaluate(`(async()=>{
        setPanMode(false);setSelectedGroups([]);document.body.classList.add('inspector-hidden');applyDocumentsView('list');
        Object.keys(groupDefaults).forEach((key,index)=>{groupPositions[key]=[5000+index*600,100];manuallyPlaced.add(key)});
        groupPositions.documents=[150,100];groupPositions.commands=[750,100];groupSizes.documents=[560,500];groupSizes.commands=[330,520];applyGroupSizes();applyGroupPositions();
        scale=${zoom};panX=180-150*scale;panY=80-100*scale;setWorld();await ${nextFrames};
        const a=document.querySelector('.g-docs').getBoundingClientRect(),b=document.querySelector('.g-commands').getBoundingClientRect();
        return{start:{x:a.left-8,y:a.top-8},end:{x:b.right+8,y:a.top+20},positions:{documents:[...groupPositions.documents],commands:[...groupPositions.commands]}};
      })()`);
      await nativePointer('mousePressed',setup.start,{button:'right',clickCount:1});
      await nativePointer('mouseMoved',setup.end,{button:'right',buttons:2});
      await nativePointer('mouseReleased',setup.end,{button:'right',clickCount:1});
      assert.deepEqual(await evaluate("[...selectedGroups].sort()"),['commands','documents'],`${zoom}: marquee selection`);
      const header=await evaluate("(()=>{const r=document.querySelector('.g-docs .group-title').getBoundingClientRect();return{x:r.left+Math.min(30,r.width/3),y:r.top+r.height/2}})()");
      const end={x:header.x+60,y:header.y+30};
      await nativePointer('mousePressed',header,{button:'left',clickCount:1,modifiers:1});
      await nativePointer('mouseMoved',end,{button:'left',buttons:1,modifiers:1});
      await nativePointer('mouseReleased',end,{button:'left',clickCount:1,modifiers:1});
      const moved=await evaluate("({documents:[...groupPositions.documents],commands:[...groupPositions.commands]})");
      for(const key of ['documents','commands']){
        assert.ok(Math.abs(moved[key][0]-setup.positions[key][0]-60/zoom)<.6,`${zoom}/${key}: horizontal multi-drag`);
        assert.ok(Math.abs(moved[key][1]-setup.positions[key][1]-30/zoom)<.6,`${zoom}/${key}: vertical multi-drag`);
      }
      const second=await evaluate("(()=>{const r=document.querySelector('.g-docs .group-title').getBoundingClientRect();return{x:r.left+Math.min(30,r.width/3),y:r.top+r.height/2}})()");
      await evaluate("documentFilesView.searchInput.focus({preventScroll:true})");
      await nativePointer('mousePressed',second,{button:'left',clickCount:1});
      await nativePointer('mouseMoved',{x:second.x+15,y:second.y+12},{button:'left',buttons:1});
      await nativeKey('Escape','Escape',27);
      await nativePointer('mouseReleased',second,{button:'left',clickCount:1});
      const cancelled=await evaluate("({documents:[...groupPositions.documents],commands:[...groupPositions.commands]})");
      for(const key of ['documents','commands'])for(let axis=0;axis<2;axis++)assert.ok(Math.abs(cancelled[key][axis]-moved[key][axis])<.001,`${zoom}: Escape did not restore ${key}`);
      const resize=await evaluate("(()=>{const group=document.querySelector('.g-docs'),r=group.querySelector('.group-resize[data-edge=nw]').getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2,width:group.offsetWidth,height:group.offsetHeight}})()");
      const finish={x:resize.x-18,y:resize.y-12};
      await nativePointer('mousePressed',resize,{button:'left',clickCount:1,modifiers:1});
      await nativePointer('mouseMoved',finish,{button:'left',buttons:1,modifiers:1});
      await nativePointer('mouseReleased',finish,{button:'left',clickCount:1,modifiers:1});
      const size=await evaluate("(()=>{const group=document.querySelector('.g-docs');return{width:group.offsetWidth,height:group.offsetHeight}})()");
      assert.ok(Math.abs(size.width-resize.width-18/zoom)<.6,`${zoom}: corner resize width`);
      assert.ok(Math.abs(size.height-resize.height-12/zoom)<.6,`${zoom}: corner resize height`);
    }
  }finally{
    await evaluate(`(()=>{finishDrag(null,true);setSelectedGroups([]);groupPositions=${JSON.stringify(backup.positions)};groupSizes=${JSON.stringify(backup.sizes)};manuallyPlaced.clear();for(const key of ${JSON.stringify(backup.manual)})manuallyPlaced.add(key);applyGroupSizes();applyGroupPositions();applyDocumentsView(${JSON.stringify(backup.mode)});document.body.classList.toggle('inspector-hidden',${backup.inspector});setSessionSidebar(${backup.sidebar});scale=${backup.camera.scale};panX=${backup.camera.panX};panY=${backup.camera.panY};setWorld();clearTimeout(cameraSaveTimer);localStorage.clear();for(const [key,value]of Object.entries(${JSON.stringify(backup.storage)}))localStorage.setItem(key,value)})()`);
  }
});

test('Native fullscreen entry and Escape exit retain the camera', {timeout:10000}, async () => {
  await preparePage();
  await command('Page.bringToFront');
  const setup=await evaluate("(()=>{setPanMode(false);scale=.67;panX=221;panY=-83;setWorld();const r=document.querySelector('#fullscreen').getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2,camera:{scale,panX,panY}}})()");
  await nativePointer('mousePressed',setup,{button:'left',clickCount:1});
  await nativePointer('mouseReleased',setup,{button:'left',clickCount:1});
  await evaluate("new Promise((resolve,reject)=>{const start=performance.now();const check=()=>document.fullscreenElement?resolve():performance.now()-start>2000?reject(new Error('Fullscreen did not open')):requestAnimationFrame(check);check()})");
  assert.deepEqual(await evaluate("({scale,panX,panY})"),setup.camera);
  await nativeKey('Escape','Escape',27);
  await evaluate("new Promise((resolve,reject)=>{const start=performance.now();const check=()=>!document.fullscreenElement?resolve():performance.now()-start>2000?reject(new Error('Escape did not exit fullscreen')):requestAnimationFrame(check);check()})");
  assert.deepEqual(await evaluate("({scale,panX,panY})"),setup.camera);
});

test('A held unpinned bubble stays under the pointer when a live catalog refresh changes topology', {timeout:15000}, async () => {
  await preparePage();
  const backup=await evaluate("documentBubbleView.options.loadPositions()");
  try{
    const setup=await evaluate(`(async()=>{
      applyDocumentsView('bubble');setPanMode(false);scale=.8;panX=panY=0;setWorld();document.querySelector('.g-docs').style.zIndex='50';
      const view=documentBubbleView;view.stop();view.autoFit=false;const node=view.nodes.get('document_0');node.pinned=false;
      let r=node.el.getBoundingClientRect(),v=viewport.getBoundingClientRect();panX=400-(r.left+r.width/2-v.left);panY=250-(r.top+r.height/2-v.top);setWorld();await ${nextFrames};
      r=node.el.getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2,node:{x:node.x,y:node.y},local:{...view.camera},outer:{scale,panX,panY}};
    })()`);
    await nativePointer('mouseMoved',setup);
    await nativePointer('mousePressed',setup,{button:'left',clickCount:1});
    const refreshed=await evaluate(`(()=>{
      config={...config,nodes:[...config.nodes,{id:'document_live_added',kind:'document',path:'docs/live-added.md',label:'docs/live-added.md',status:'idle'}],document_links:[...config.document_links,{from:'document_0',to:'document_live_added'}]};
      renderCatalog(false);renderState(state);const view=documentBubbleView,node=view.nodes.get('document_0');
      return{node:{x:node.x,y:node.y},local:{...view.camera},outer:{scale,panX,panY},owner:view.drag?.node.id,pinned:node.pinned,added:view.nodes.has('document_live_added')};
    })()`);
    assert.deepEqual(refreshed.node,setup.node);assert.deepEqual(refreshed.local,setup.local);assert.deepEqual(refreshed.outer,setup.outer);
    assert.equal(refreshed.owner,'document_0');assert.equal(refreshed.pinned,false);assert.equal(refreshed.added,true);
    const end={x:setup.x+16,y:setup.y+8};
    await nativePointer('mouseMoved',end,{button:'left',buttons:1});
    await nativePointer('mouseReleased',end,{button:'left',clickCount:1});
    const moved=await evaluate("(()=>{const node=documentBubbleView.nodes.get('document_0');return{x:node.x,y:node.y,pinned:node.pinned}})()");
    assert.ok(Math.abs(moved.x-setup.node.x-20/setup.local.scale)<.1);assert.ok(Math.abs(moved.y-setup.node.y-10/setup.local.scale)<.1);assert.equal(moved.pinned,true);
  }finally{await evaluate(`documentBubbleView.options.savePositions(${JSON.stringify(backup)})`)}
});

test('Recorded documents survive source removal in List, Bubbles and usage counts without leaking into another route', {timeout:15000}, async () => {
  await preparePage();await evaluate(`(${documentReadFixture.toString()})(true)`);
  const result=await evaluate(`(async()=>{
    const recorded=steps.slice(),runId=viewRunId,camera={scale,panX,panY};
    recorded[recorded.length-1]={...recorded.at(-1),nodes:{...recorded.at(-1).nodes,unobserved_history_only:{id:'unobserved_history_only',kind:'document',label:'never-read.md',path:'docs/never-read.md'}}};
    config={...config,nodes:config.nodes.filter(node=>node.id!=='document_0')};renderCatalog(false);renderState(recorded.at(-1));await ${nextFrames};
    const inspect=()=>({list:Boolean(documentFilesView.treeContainer.querySelector('[data-node-id="document_0"]')),bubble:documentBubbleView.nodes.has('document_0'),count:state.nodes.document_0?.usage_count??0,total:documentsForView().length,unobserved:Boolean(documentFilesView.treeContainer.querySelector('[data-node-id="unobserved_history_only"]')||documentBubbleView.nodes.has('unobserved_history_only'))});
    const retained=inspect();
    viewRunId='without-retired-document';steps=[{run_id:viewRunId,prompt:'New route',orchestrator_phase:'running',nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),edges:[],event:{type:'PROMPT_START',prompt:'New route'}}];showStep(0);await ${nextFrames};
    const other={list:Boolean(documentFilesView.treeContainer.querySelector('[data-node-id="document_0"]')),bubble:documentBubbleView.nodes.has('document_0'),present:Boolean(state.nodes.document_0),readCount:state.nodes[commandTemplateFor('read').id]?.usage_count||0};
    steps=recorded;viewRunId=runId;showStep(1);await ${nextFrames};
    return{retained,other,restored:inspect(),sameCamera:JSON.stringify(camera)===JSON.stringify({scale,panX,panY})};
  })()`);
  assert.deepEqual(result.retained,{list:true,bubble:true,count:1,total:documentNodes.length,unobserved:false});
  assert.deepEqual(result.other,{list:false,bubble:false,present:false,readCount:0});
  assert.deepEqual(result.restored,result.retained);assert.equal(result.sameCamera,true);
});

test('Pending settings save blocks further edits and a failed save retains the draft and restores editing', {timeout:15000}, async () => {
  await preparePage();
  await evaluate(`(async()=>{
    window.__originalSettingsRequest=blockSettings.api.request;
    const model={revision:'fixture',documents:[{path:'docs',include:['**/*.md']}],tools:[],hidden_commands:[],block_layouts:{},capabilities:{mcp:[],skills:[]},memory_systems:[]};
    blockSettings.api.request=(url,body)=>body?new Promise((resolve,reject)=>{window.__rejectSettingsSave=reject}):Promise.resolve(structuredClone(model));
    await blockSettings.open('documents',document.querySelector('.g-docs .group-settings-button'));
    const field=document.querySelector('[aria-label="Document source path 1"]');field.focus();field.select();
  })()`);
  try{
    await command('Input.insertText',{text:'docs-before-save'});
    await evaluate("window.__settingsSaveInFlight=blockSettings.save();true");
    const pending=await evaluate("({busy:blockSettings.busy,path:blockSettings.model.documents[0].path,disabled:document.querySelector('[aria-label=\"Document source path 1\"]').disabled,allDisabled:[...blockSettings.body.querySelectorAll('input,select,textarea,button')].every(control=>control.disabled)})");
    assert.deepEqual(pending,{busy:true,path:'docs-before-save',disabled:true,allDisabled:true});
    await evaluate("document.querySelector('[aria-label=\"Document source path 1\"]').focus()");
    await command('Input.insertText',{text:'must-not-overwrite-draft'});
    assert.equal(await evaluate("blockSettings.model.documents[0].path"),'docs-before-save');
    await evaluate("window.__rejectSettingsSave(new Error('Fixture save failure'));window.__settingsSaveInFlight");
    const failed=await evaluate("({busy:blockSettings.busy,dirty:blockSettings.dirty,path:blockSettings.model.documents[0].path,disabled:document.querySelector('[aria-label=\"Document source path 1\"]').disabled})");
    assert.deepEqual(failed,{busy:false,dirty:true,path:'docs-before-save',disabled:false});
    await evaluate("(()=>{const field=document.querySelector('[aria-label=\"Document source path 1\"]');field.focus();field.select()})()");
    await command('Input.insertText',{text:'docs-after-failure'});
    assert.equal(await evaluate("blockSettings.model.documents[0].path"),'docs-after-failure');
  }finally{await evaluate("blockSettings.api.request=window.__originalSettingsRequest;blockSettings.busy=false;blockSettings.dirty=false;blockSettings.close();delete window.__rejectSettingsSave;delete window.__settingsSaveInFlight;delete window.__originalSettingsRequest")}
});

test('Memory help advances one field at a time without resizing or changing drafts, and preserves bank filters after a failed save', {timeout:15000}, async () => {
  await preparePage();
  await evaluate(`(async()=>{
    window.__memorySettingsRequest=blockSettings.api.request;
    const model={revision:'fixture',documents:[],tools:[],hidden_commands:[],block_layouts:{},capabilities:{mcp:[],skills:[]},
      memory_auth_configured:{fixture:true},
      memory_systems:[{id:'fixture',name:'Experience',provider:'hindsight',endpoint:'http://127.0.0.1:8888',bank_ids:['original'],categories:[],stores:[],match:{tool_names:[]}}]};
    blockSettings.api.request=(url,body)=>body?Promise.reject(new Error('Fixture save failure')):Promise.resolve(structuredClone(model));
    await blockSettings.open('memory',document.querySelector('.g-memory .group-settings-button'));
    window.__memoryBeforeGuide=JSON.stringify(blockSettings.model);
    window.__memoryGuideBounds=(()=>{const {x,y,width,height}=blockSettings.dialog.getBoundingClientRect();return{x,y,width,height}})();
    window.__memoryGuideScroll=blockSettings.body.scrollHeight;
    const identity=document.querySelector('input[aria-label^="Stable system ID"]');identity.focus();identity.select();
  })()`);
  try {
    await command('Input.insertText',{text:'replacement-id'});
    assert.deepEqual(await evaluate("({id:blockSettings.model.memory_systems[0].id,visible:document.querySelector('input[aria-label^=\"Stable system ID\"]').value,dirty:blockSettings.dirty})"),{id:'fixture',visible:'fixture',dirty:false});
    await evaluate("blockSettings.helpButton.click()");
    const explained=await evaluate(`(()=>{
      const {x,y,width,height}=blockSettings.dialog.getBoundingClientRect();
      return {unchanged:JSON.stringify(blockSettings.model)===window.__memoryBeforeGuide,dirty:blockSettings.dirty,
        advanced:document.querySelector('.memory-settings-entry details').open,
        bounds:{x,y,width,height},scrollHeight:blockSettings.body.scrollHeight,
        singleTarget:document.querySelectorAll('.settings-guide-target').length===1};
    })()`);
    assert.equal(explained.unchanged,true);assert.equal(explained.dirty,false);assert.equal(explained.advanced,false);assert.equal(explained.singleTarget,true);
    assert.deepEqual(explained.bounds,await evaluate('window.__memoryGuideBounds'));
    assert.equal(explained.scrollHeight,await evaluate('window.__memoryGuideScroll'));
    const navigation=await evaluate(`(()=>{
      const first=document.querySelector('.settings-guide-target');
      document.querySelector('.settings-guide-next').click();
      const changed=document.querySelector('.settings-guide-target')!==first;
      document.querySelector('.settings-guide-back').click();
      return {changed,returned:document.querySelector('.settings-guide-target')===first};
    })()`);
    assert.deepEqual(navigation,{changed:true,returned:true});
    const expanded=await evaluate(`(()=>{
      for(let guard=0;guard<30&&!document.querySelector('.memory-settings-entry details').open;guard++)document.querySelector('.settings-guide-next').click();
      const {x,y,width,height}=blockSettings.dialog.getBoundingClientRect();
      return {bounds:{x,y,width,height},expanded:document.querySelector('.memory-settings-entry details').open,scrollable:blockSettings.body.scrollHeight>blockSettings.body.clientHeight};
    })()`);
    assert.equal(expanded.expanded,true);assert.equal(expanded.scrollable,true);
    assert.deepEqual(expanded.bounds,await evaluate('window.__memoryGuideBounds'));
    await nativeKey('Escape','Escape',27);
    assert.deepEqual(await evaluate("({open:blockSettings.dialog.open,focus:document.activeElement===blockSettings.helpButton,advanced:document.querySelector('.memory-settings-entry details').open,targets:document.querySelectorAll('.settings-guide-target').length})"),{open:true,focus:true,advanced:false,targets:0});
    await evaluate("(()=>{const banks=document.querySelector('textarea[aria-label^=\"Bank IDs\"]');banks.focus();banks.select()})()");
    await command('Input.insertText',{text:' bank-one \n bank-two \n'});
    assert.deepEqual(await evaluate("blockSettings.model.memory_systems[0].bank_ids"),['bank-one','bank-two']);
    await evaluate("blockSettings.helpButton.click()");
    await evaluate("blockSettings.save()");
    const failed=await evaluate(`({
      dirty:blockSettings.dirty,busy:blockSettings.busy,
      banks:blockSettings.model.memory_systems[0].bank_ids,
      draft:document.querySelector('textarea[aria-label^="Bank IDs"]').value,
      id:blockSettings.model.memory_systems[0].id,
      privatePathAbsent:!Object.hasOwn(blockSettings.model.memory_systems[0],'token_file')
    })`);
    assert.deepEqual(failed,{dirty:true,busy:false,banks:['bank-one','bank-two'],draft:' bank-one \n bank-two \n',id:'fixture',privatePathAbsent:true});
    assert.equal(await evaluate("blockSettings.guide.active"),false,'A failed save left stale field help over the draft');
    await evaluate("(()=>{const identity=document.querySelector('input[aria-label^=\"Stable system ID\"]');identity.focus();identity.select()})()");
    await command('Input.insertText',{text:'replacement-after-failure'});
    assert.equal(await evaluate("document.querySelector('input[aria-label^=\"Stable system ID\"]').value"),'fixture');
  } finally {
    await evaluate("blockSettings.api.request=window.__memorySettingsRequest;blockSettings.busy=false;blockSettings.dirty=false;blockSettings.close();delete window.__memorySettingsRequest;delete window.__memoryBeforeGuide;delete window.__memoryGuideBounds;delete window.__memoryGuideScroll");
  }
});

test('Memory connections add and remove independently while retaining invalid edits in another entry', {timeout:15000}, async () => {
  await preparePage();
  await evaluate(`(async()=>{
    window.__memoryRemovalRequest=blockSettings.api.request;
    const model={revision:'fixture',documents:[],tools:[],hidden_commands:[],block_layouts:{},capabilities:{mcp:[],skills:[]},
      memory_auth_configured:{fixture:true},
      memory_systems:[{id:'fixture',name:'Experience',provider:'hindsight',endpoint:'http://127.0.0.1:8888',bank_ids:['original'],categories:[],stores:[],match:{tool_names:[]}}]};
    blockSettings.api.request=()=>Promise.resolve(structuredClone(model));
    await blockSettings.open('memory',document.querySelector('.g-memory .group-settings-button'));
    const add=provider=>{document.querySelector('[aria-label="Add memory system"]').click();document.querySelectorAll('#memory-system-add-menu button')[provider==='hindsight'?0:1].click()};
    add('hindsight');add('custom');
    const entries=document.querySelectorAll('.memory-settings-entry');
    entries[1].querySelector('summary').click();
    const categories=entries[1].querySelector('textarea.memory-settings-json');categories.focus();categories.select();
  })()`);
  try {
    await command('Input.insertText',{text:'['});
    await evaluate("document.querySelectorAll('.memory-settings-remove')[2].click()");
    assert.deepEqual(await evaluate(`({
      remaining:[...document.querySelectorAll('input[aria-label^="Stable system ID"]')].map(input=>input.value),
      invalidDraft:document.querySelectorAll('.memory-settings-entry')[1].querySelector('textarea.memory-settings-json').value,
      blocked:blockSettings.saveButton.disabled,banks:blockSettings.model.memory_systems[0].bank_ids
    })`).then(result=>({...result,remaining:[result.remaining[0],result.remaining.length]})),
    {remaining:['fixture',2],invalidDraft:'[',blocked:true,banks:['original']});
    await evaluate("document.querySelectorAll('.memory-settings-remove')[1].click()");
    assert.deepEqual(await evaluate("({ids:blockSettings.model.memory_systems.map(system=>system.id),canSave:!blockSettings.saveButton.disabled})"),{ids:['fixture'],canSave:true});
    await evaluate("document.querySelector('.memory-settings-remove').click()");
    assert.deepEqual(await evaluate("({count:document.querySelectorAll('.memory-settings-entry').length,empty:!document.querySelector('.memory-settings-empty').hidden,canAdd:!document.querySelector('[aria-label=\"Add memory system\"]').disabled})"),{count:0,empty:true,canAdd:true});
    await evaluate("document.querySelector('[aria-label=\"Add memory system\"]').click();document.querySelectorAll('#memory-system-add-menu button')[1].click()");
    assert.deepEqual(await evaluate("({count:document.querySelectorAll('.memory-settings-entry').length,empty:document.querySelector('.memory-settings-empty').hidden,provider:blockSettings.model.memory_systems[0].provider})"),{count:1,empty:true,provider:'custom'});
  } finally {
    await evaluate("blockSettings.api.request=window.__memoryRemovalRequest;blockSettings.dirty=false;blockSettings.close();delete window.__memoryRemovalRequest");
  }
});

test('Removing a memory connection drops unused cards but retains recorded calls until switching routes', async () => {
  await preparePage();
  const result=await evaluate(`(()=>{
    const memory=config.nodes.find(node=>node.kind==='memory'),unused={...memory,id:'memory_unused',memory_system_id:'unused',label:'Unused memory'};
    config.nodes.push(unused);
    viewMode='saved';viewRunId='memory-removal';
    const base={...structuredClone(state),run_id:viewRunId,nodes:{...state.nodes,[memory.id]:memory,[unused.id]:unused},orchestrator_phase:'complete'};
    const event={tool:'memory_recall',call_id:'recorded-memory-call',memory_system_id:memory.memory_system_id,memory_bank_id:'demo',memory_endpoint:memory.endpoint,memory_operation:'recall'};
    steps=[
      {...base,timestamp:'2026-01-01T00:00:00Z',event:{...event,type:'TOOL_CALL_START'}},
      {...base,timestamp:'2026-01-01T00:00:01Z',event:{...event,type:'TOOL_CALL_END',success:true,result:'recorded-memory-result'}}
    ];
    showStep(1);
    const before=Boolean(document.getElementById(unused.id));
    config.nodes=config.nodes.filter(node=>node.kind!=='memory');
    renderCatalog(false);renderState(state);
    const removed=!document.getElementById(unused.id);
    const retained=Boolean(document.getElementById(memory.id)),usage=state.nodes[memory.id]?.usage_count;
    const calls=buildCallSequence(routeEventsFor(state)),output=calls[0]?.endEvent.result;
    viewRunId='another-route';steps=[];currentStepIndex=-1;
    renderState({run_id:viewRunId,nodes:{},edges:[],orchestrator_phase:'idle'});
    return{before,removed,retained,usage,output,cleared:!document.getElementById(memory.id)};
  })()`);
  assert.deepEqual(result,{before:true,removed:true,retained:true,usage:1,output:'recorded-memory-result',cleared:true});
});

test('Large activity history preserves every call, raw-details state and fresh timing/output after live replacement', {timeout:20000}, async () => {
  await preparePage();await evaluate(`(${documentReadFixture.toString()})(false)`);
  await evaluate(`(async()=>{
    const base=structuredClone(steps[0]);steps=[];
    for(let i=0;i<1200;i++)for(const end of [false,true]){const snapshot={...base,timestamp:new Date(Date.parse(base.timestamp)+steps.length*1000).toISOString(),event:{type:end?'TOOL_CALL_END':'TOOL_CALL_START',tool:'read',call_id:'large-'+i,input:JSON.stringify({path:'docs/standards/guide.md'}),...(end?{success:true,result:'recorded-large-output-'+i}:{})}};steps.push(snapshot)}
    showStep(steps.length-1);await ${nextFrames};openGroupResourceDetails('commands');
    document.querySelector('.activity-call-item').click();document.querySelector('.activity-raw-details').open=true;document.querySelector('.activity-btn-step').click();
  })()`);
  const selected=await evaluate("({listed:document.querySelectorAll('.activity-call-item').length,ordinal:document.querySelector('.activity-call-item.selected .activity-call-item-num').textContent,raw:document.querySelector('.activity-raw-details').open,output:document.querySelector('.activity-detail-body').textContent})");
  assert.equal(selected.listed,1200);assert.equal(selected.ordinal,'#2');assert.equal(selected.raw,true);assert.ok(selected.output.includes('recorded-large-output-1'));
  await evaluate(`(async()=>{
    steps[2]={...steps[2],timestamp:'2026-01-01T00:10:00Z'};steps[3]={...steps[3],event:{...steps[3].event,duration_ms:5678,result:'corrected-recorded-output'}};
    renderState(steps.at(-1));await ${nextFrames};
  })()`);
  const replaced=await evaluate("({timing:document.querySelector('.activity-call-item[data-step=\"2\"] .activity-call-item-timing').textContent,output:document.querySelector('.activity-detail-body').textContent,raw:document.querySelector('.activity-raw-details').open,listed:document.querySelectorAll('.activity-call-item').length})");
  assert.equal(replaced.timing,'+600.0 s · 5.68 s');assert.ok(replaced.output.includes('corrected-recorded-output'));assert.equal(replaced.raw,true);assert.equal(replaced.listed,1200);
  await evaluate("closeResourceDetails();openGroupResourceDetails('tools')");
  assert.equal(await evaluate("document.querySelectorAll('.activity-call-item').length"),0,'A different resource inherited the Commands call list');
  await evaluate("closeResourceDetails();openResourceDetails(commandTemplateFor('read').id)");
  const single=await evaluate("({raw:document.querySelector('.activity-raw-details').open,output:document.querySelector('.activity-detail-body').textContent,scope:resourceDialog.dataset.scope})");
  assert.equal(single.scope,'single');assert.equal(single.raw,false);assert.ok(single.output.includes('recorded-large-output-1199'));
  await evaluate("closeResourceDetails()");
});

test('Live updates retain every event during native panning, respect manual inspection and catch up without moving the camera', {timeout:15000}, async () => {
  await preparePage();await evaluate(`(${documentReadFixture.toString()})(true)`);
  const setup=await evaluate(`(()=>{
    liveSteps=steps.map((step,index)=>({...step,step_index:index}));liveSnapshot=liveSteps.at(-1);viewMode='live';steps=liveSteps;viewRunId=liveSnapshot.run_id;showStep(steps.length-1);
    scale=.5;panX=viewport.clientWidth+100;panY=viewport.clientHeight+100;setWorld();const r=viewport.getBoundingClientRect();
    return{x:r.left+400,y:r.top+250,panX,panY};
  })()`);
  const end={x:setup.x+80,y:setup.y+40};
  await nativePointer('mousePressed',setup,{button:'left',clickCount:1});
  await nativePointer('mouseMoved',end,{button:'left',buttons:1});
  const held=await evaluate(`(()=>{
    const doc=config.nodes.find(node=>node.id==='document_1'),read=commandTemplateFor('read');
    const replacement={...liveSteps[0],event:{...liveSteps[0].event,input:JSON.stringify({path:doc.path})}};
    onWsMessage({type:'STATE_UPDATE',state:liveSnapshot,history_step:replacement});
    const failed={...liveSteps[1],event:{...liveSteps[1].event,success:false,result:'Corrected failure',duration_ms:1234}};
    onWsMessage({type:'STATE_UPDATE',state:liveSnapshot,history_step:failed});
    return{old:state.nodes.document_0.usage_count,next:state.nodes.document_1.usage_count,recordedPath:JSON.parse(liveSteps[0].event.input).path,recordedFailure:liveSteps[1].event.success,owner:drag?.kind};
  })()`);
  assert.equal(held.old,1);assert.equal(held.next,0);assert.equal(held.recordedPath,documentNodes[1].path);assert.equal(held.recordedFailure,false);assert.equal(held.owner,'pan');
  await nativePointer('mouseReleased',end,{button:'left',clickCount:1});
  const corrected=await evaluate(`(async()=>{
    for(let i=0;i<120&&state.nodes.document_1.status!=='error';i++)await new Promise(requestAnimationFrame);
    return{old:state.nodes.document_0.usage_count,next:state.nodes.document_1.usage_count,read:state.nodes[commandTemplateFor('read').id].usage_count,status:state.nodes.document_1.status,last:state.nodes.document_1.last_step};
  })()`);
  assert.deepEqual(corrected,{old:0,next:1,read:1,status:'error',last:1});
  await nativePointer('mousePressed',end,{button:'left',clickCount:1});
  await evaluate(`(()=>{
    const start={...liveSnapshot,step_index:2,timestamp:'2026-01-01T00:00:04Z',event:{type:'TOOL_CALL_START',tool:'read',call_id:'new-live-call',input:JSON.stringify({path:config.nodes.find(n=>n.id==='document_2').path})}};
    const finish={...start,step_index:3,timestamp:'2026-01-01T00:00:05Z',event:{type:'TOOL_CALL_END',tool:'read',call_id:'new-live-call',success:true,result:'Second output'}};
    onWsMessage({type:'STATE_UPDATE',state:start,history_step:start});onWsMessage({type:'STATE_UPDATE',state:finish,history_step:finish});showStep(0);
  })()`);
  await nativePointer('mouseReleased',end,{button:'left',clickCount:1});
  const result=await evaluate(`(async()=>{
    await new Promise(resolve=>setTimeout(resolve,220));
    const read=commandTemplateFor('read'),pinned={index:currentStepIndex,read:state.nodes[read.id].usage_count,total:steps.length};
    showLive();const followed={index:currentStepIndex,read:state.nodes[read.id].usage_count,second:state.nodes.document_2.usage_count,firstStatus:state.nodes.document_1.status};
    const next={run_id:'fresh-live-request',step_index:0,prompt:'New request',orchestrator_phase:'running',nodes:Object.fromEntries(config.nodes.map(n=>[n.id,{...n,status:'idle'}])),edges:[],timestamp:'2026-01-01T00:01:00Z',event:{type:'PROMPT_START',prompt:'New request'}};
    onWsMessage({type:'STATE_UPDATE',state:next,history_step:next});
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    return{pinned,followed,fresh:{read:state.nodes[read.id].usage_count,old:state.nodes.document_1.usage_count},camera:{panX,panY},owner:drag?.kind||null};
  })()`);
  assert.deepEqual(result.pinned,{index:0,read:1,total:4});assert.deepEqual(result.followed,{index:3,read:2,second:1,firstStatus:'error'});
  assert.deepEqual(result.fresh,{read:0,old:0});assert.equal(result.owner,null);assert.ok(Math.abs(result.camera.panX-setup.panX-80)<.1);assert.ok(Math.abs(result.camera.panY-setup.panY-40)<.1);
});

test('Dialog headers drag within the viewport and close controls remain usable', async () => {
  await preparePage();
  const start=await evaluate(`(async()=>{
    window.__dragRequest=blockSettings.api.request;
    blockSettings.api.request=async()=>({revision:'fixture',documents:[],tools:[],hidden_commands:[],block_layouts:{},capabilities:{mcp:[],skills:[]},memory_systems:[]});
    await blockSettings.open('commands',document.querySelector('.g-commands .group-settings-button'));
    const dialog=blockSettings.dialog,head=dialog.querySelector('.block-settings-heading'),close=dialog.querySelector('#block-settings-close');
    const r=dialog.getBoundingClientRect(),h=head.getBoundingClientRect(),b=close.getBoundingClientRect(),s=close.querySelector('svg').getBoundingClientRect();
    return {x:h.left+40,y:h.top+15,left:r.left,top:r.top,centerX:(b.left+b.right-s.left-s.right)/2,centerY:(b.top+b.bottom-s.top-s.bottom)/2};
  })()`);
  try {
    assert.ok(Math.abs(start.centerX)<1 && Math.abs(start.centerY)<1);
    await nativePointer('mouseMoved',start);
    await nativePointer('mousePressed',start,{button:'left',clickCount:1});
    await nativePointer('mouseMoved',{x:start.x+80,y:start.y+35},{button:'left',buttons:1});
    await nativePointer('mouseReleased',{x:start.x+80,y:start.y+35},{button:'left',clickCount:1});
    const moved=await evaluate("(()=>{const r=blockSettings.dialog.getBoundingClientRect();return {left:r.left,top:r.top}})()");
    assert.ok(moved.left>start.left+20 && moved.top>start.top+10);
    await evaluate("new Promise(resolve=>{blockSettings.dialog.addEventListener('close',resolve,{once:true});document.querySelector('#block-settings-close').click()})");
    assert.equal(await evaluate("blockSettings.dialog.open"),false);
    assert.equal(await evaluate("blockSettings.dialog.style.translate"),'');
  } finally {
    await evaluate("blockSettings.api.request=window.__dragRequest;if(blockSettings.dialog.open)blockSettings.close();delete window.__dragRequest");
  }
});

test('Compact tool settings preserve independent drafts, footer selection and window bounds through guidance and failed saves', {timeout:15000}, async () => {
  await preparePage();
  await evaluate(`(async()=>{
    window.__compactToolsRequest=blockSettings.api.request;
    const model={revision:'fixture',documents:[],tools:[],hidden_commands:[],block_layouts:{capabilities:'list',tools:'free'},memory_systems:[],
      capabilities:{mcp:[{name:'existing-reference',description:'Original',tool_names:['existing_tool']}],skills:[]}};
    const inventory={mcp:[{name:'codegraph',connection:'configured',description:'Symbol navigation'}],skills:[{name:'review',path:'skill://review'}],integrations:[]};
    blockSettings.api.request=(url,body)=>body?new Promise((resolve,reject)=>{window.__compactToolsReject=reject}):Promise.resolve(url==='/api/capabilities'?{inventory,discovery:inventory,runtime_inventory:{mcp:[{name:'codegraph',connection:'connected'}]}}:structuredClone(model));
    await blockSettings.open('capabilities',document.querySelector('.g-capabilities .group-settings-button'));
    window.__compactToolsBounds=JSON.stringify((({x,y,width,height})=>({x,y,width,height}))(blockSettings.dialog.getBoundingClientRect()));
    window.__compactToolsCamera={scale,panX,panY};
    document.querySelector('.block-layout-picker input[value="list"]').focus();
  })()`);
  try {
    await nativeKey('ArrowRight','ArrowRight',39);
    assert.deepEqual(await evaluate("({mode:blockSettings.model.block_layouts.capabilities,other:blockSettings.model.block_layouts.tools,checked:document.querySelector('.block-layout-picker input:checked').value})"),{mode:'grid',other:'free',checked:'grid'});
    await evaluate(`(()=>{
      const add=key=>{document.querySelector('[aria-label="Add tool registration"]').click();document.querySelectorAll('#capability-add-menu button')[key==='mcp'?0:1].click()};
      add('mcp');
      const name=document.querySelector('[aria-label="MCP name 2"]');name.focus();name.select();
    })()`);
    await command('Input.insertText',{text:'new-reference'});
    await evaluate("(()=>{const input=document.querySelector('[aria-label=\"MCP tool names 2\"]');input.focus();input.select()})()");
    await command('Input.insertText',{text:'one, two, '});
    await evaluate("document.querySelector('[aria-label=\"Add tool registration\"]').click();document.querySelectorAll('#capability-add-menu button')[1].click();document.querySelector('[aria-label=\"Remove MCP registration: existing-reference\"]').click()");
    assert.deepEqual(await evaluate("({mcp:blockSettings.model.capabilities.mcp.map(item=>item.name),skills:blockSettings.model.capabilities.skills.length,raw:document.querySelector('[aria-label=\"MCP tool names 2\"]').value})"),{mcp:['new-reference'],skills:1,raw:'one, two, '});
    const guided=await evaluate(`(()=>{
      const before=JSON.stringify(blockSettings.model);blockSettings.helpButton.click();
      for(let i=0;i<30&&blockSettings.guide.active;i++)document.querySelector('.settings-guide-next').click();
      return {unchanged:JSON.stringify(blockSettings.model)===before,bounds:JSON.stringify((({x,y,width,height})=>({x,y,width,height}))(blockSettings.dialog.getBoundingClientRect()))===window.__compactToolsBounds,camera:{scale,panX,panY}};
    })()`);
    assert.equal(guided.unchanged,true);assert.equal(guided.bounds,true);
    assert.deepEqual(guided.camera,await evaluate('window.__compactToolsCamera'));
    await evaluate("window.__compactToolsSave=blockSettings.save();undefined");
    assert.deepEqual(await evaluate("({busy:blockSettings.busy,radiosLocked:[...document.querySelectorAll('.block-layout-picker input')].every(input=>input.disabled)})"),{busy:true,radiosLocked:true});
    await evaluate("window.__compactToolsReject(new Error('Fixture save failure'));window.__compactToolsSave");
    assert.deepEqual(await evaluate("({busy:blockSettings.busy,dirty:blockSettings.dirty,mode:blockSettings.model.block_layouts.capabilities,raw:document.querySelector('[aria-label=\"MCP tool names 2\"]').value,editable:[...document.querySelectorAll('.block-layout-picker input')].every(input=>!input.disabled)})"),{busy:false,dirty:true,mode:'grid',raw:'one, two, ',editable:true});
  } finally {
    await evaluate("blockSettings.api.request=window.__compactToolsRequest;blockSettings.dirty=false;blockSettings.busy=false;blockSettings.close();delete window.__compactToolsRequest;delete window.__compactToolsReject;delete window.__compactToolsSave;delete window.__compactToolsBounds;delete window.__compactToolsCamera");
  }
});

test('Failed discovery and document-move requests leave their settings actions retryable', {timeout:15000}, async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const request=blockSettings.api.request,confirm=window.confirm;
    const model={revision:'fixture',documents:[],tools:[],hidden_commands:[],block_layouts:{},capabilities:{mcp:[],skills:[]},memory_systems:[]};
    blockSettings.api.request=(url,body)=>body||url.startsWith('/api/document?')?Promise.reject(new Error('Fixture request failed')):Promise.resolve(url==='/api/capabilities'?{inventory:{mcp:[],skills:[],integrations:[]}}:structuredClone(model));
    window.confirm=()=>true;
    try{
      await blockSettings.open('capabilities',document.querySelector('.g-capabilities .group-settings-button'));
      const discovery=blockSettings.body.querySelector('[aria-label="Search MCP & skills again"]');
      await blockSettings.refreshCapabilities();const discovered={disabled:discovery.disabled,busy:blockSettings.busy};blockSettings.close();
      await blockSettings.open('documents',document.querySelector('.g-docs .group-settings-button'));
      const move=[...blockSettings.body.querySelectorAll('button')].find(button=>button.textContent==='Move file…');
      await blockSettings.moveDocument(config.nodes.find(node=>node.kind==='document'),'docs/moved.md');
      return{discovered,moved:{disabled:move.disabled,busy:blockSettings.busy}};
    }finally{blockSettings.api.request=request;window.confirm=confirm;blockSettings.dirty=false;blockSettings.busy=false;blockSettings.close()}
  })()`);
  assert.deepEqual(result,{discovered:{disabled:false,busy:false},moved:{disabled:false,busy:false}});
});

test('Native document zoom preserves its pointer anchor and drains every queued live call without changing the main camera', {timeout:15000}, async () => {
  await preparePage();await evaluate(`(${documentReadFixture.toString()})(true)`);
  const setup=await evaluate(`(()=>{
    liveSteps=steps.map((step,index)=>({...step,step_index:index}));liveSnapshot=liveSteps.at(-1);steps=liveSteps;viewMode='live';viewRunId=liveSnapshot.run_id;selectedCallStep=null;showStep(steps.length-1);
    documentBubbleView.stop();documentBubbleView.autoFit=false;scale=.75;panX=-500;panY=20;setWorld();
    const r=documentBubbleView.host.getBoundingClientRect(),client={x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)},point=documentBubbleView.localPoint({clientX:client.x,clientY:client.y}),camera=documentBubbleView.camera;
    return{client,anchor:{x:(point.x-camera.x)/camera.scale,y:(point.y-camera.y)/camera.scale},localScale:camera.scale,root:{scale,panX,panY}};
  })()`);
  await nativePointer('mouseWheel',setup.client,{deltaX:0,deltaY:120});
  const held=await evaluate(`(()=>{
    for(let n=1;n<=5;n++){
      const start={...liveSnapshot,step_index:liveSteps.length,timestamp:new Date(1767225600000+n*3000).toISOString(),event:{type:'TOOL_CALL_START',tool:'read',call_id:'zoom-burst-'+n,input:JSON.stringify({path:config.nodes.find(node=>node.id==='document_'+n).path})}};
      const finish={...start,step_index:start.step_index+1,timestamp:new Date(1767225601000+n*3000).toISOString(),event:{type:'TOOL_CALL_END',tool:'read',call_id:'zoom-burst-'+n,success:true,result:'Zoom burst output '+n}};
      onWsMessage({type:'STATE_UPDATE',state:start,history_step:start});onWsMessage({type:'STATE_UPDATE',state:finish,history_step:finish});
    }
    return{shown:state.nodes[commandTemplateFor('read').id].usage_count,retained:buildCallSequence(liveSteps).length,shownEvents:steps.length,retainedEvents:liveSteps.length};
  })()`);
  assert.deepEqual(held,{shown:1,retained:6,shownEvents:2,retainedEvents:12});
  const result=await evaluate(`(async()=>{
    for(let i=0;i<120&&state.nodes[commandTemplateFor('read').id].usage_count!==6;i++)await new Promise(requestAnimationFrame);
    await ${nextFrames};
    const deadline=performance.now()+10000;
    while(performance.now()<deadline&&(routePlayback.current||routePlayback.queue.length))await new Promise(requestAnimationFrame);
    const point=documentBubbleView.localPoint({clientX:${setup.client.x},clientY:${setup.client.y}}),camera=documentBubbleView.camera;
    return{root:{scale,panX,panY},localScale:camera.scale,anchor:{x:(point.x-camera.x)/camera.scale,y:(point.y-camera.y)/camera.scale},visualIdle:!routePlayback.current&&!routePlayback.queue.length,calls:state.nodes[commandTemplateFor('read').id].usage_count,outputs:buildCallSequence(steps).slice(1).map(call=>eventFor(steps[call.endStep]).result),documents:Array.from({length:5},(_,i)=>{const id='document_'+(i+1),el=documentBubbleView.getElement(id);return{count:state.nodes[id].usage_count,status:el.dataset.status,pending:el.classList.contains('arrival-pending')}})};
  })()`);
  assert.deepEqual(result.root,setup.root);assert.ok(result.localScale<setup.localScale);
  assert.ok(Math.abs(result.anchor.x-setup.anchor.x)<.1);assert.ok(Math.abs(result.anchor.y-setup.anchor.y)<.1);
  assert.equal(result.visualIdle,true);assert.equal(result.calls,6);assert.deepEqual(result.outputs,Array.from({length:5},(_,i)=>'Zoom burst output '+(i+1)));
  assert.deepEqual(result.documents,Array.from({length:5},()=>({count:1,status:'hit',pending:false})));
});

test('Block and nested-category inspectors report only their actual native, service, document and MCP calls', async () => {
  await preparePage();await evaluate(`(${documentReadFixture.toString()})(true)`);
  const result=await evaluate(`(()=>{
    config={...config,nodes:[...config.nodes,{id:'inspector-service',kind:'tool',category:'tool',group:'Fixture services',label:'Inspector service',match:{command_prefixes:['fixture-tool']}},{id:'inspector-mcp',kind:'mcp',group:'Fixture MCP group',name:'fixture_inspector',label:'Inspector MCP',tool_names:['mcp__fixture_inspector__inspect']}]};
    renderCatalog(false);
    for(const event of [{tool:'bash',call_id:'inspector-shell',input:JSON.stringify({command:'fixture-tool --inspect'})},{tool:'mcp__fixture_inspector__inspect',mcp_server:'fixture_inspector',call_id:'inspector-mcp',input:'{}'}]){
      const snapshot=steps.at(-1),start={...snapshot,timestamp:new Date(1767225600000+steps.length*1000).toISOString(),event:{...event,type:'TOOL_CALL_START'}};
      steps.push(start,{...start,timestamp:new Date(Date.parse(start.timestamp)+1000).toISOString(),event:{...event,type:'TOOL_CALL_END',success:true,result:event.tool==='bash'?'Service fixture output':'MCP fixture output'}});
    }
    showStep(steps.length-1);
    return [['commands','Commands'],['documents','Documents & references'],['tools','Tools & services'],['capabilities','MCP, skills & integrations'],[null,'Fixture MCP group']].map(([key,label])=>{
      const resource={id:key?'group:'+key:'category:'+label,kind:'category',label};select(resource.id,resource);
      return{accessed:Number($('inspector-payload').textContent.match(/\\d+/g)?.at(-1)),tools:invocationsFor(resource).map(call=>call.event.tool),visibleRows:$('inspector-invocations').querySelectorAll('.invocation-row').length,visibleLogs:$('inspector-invocations').textContent};
    });
  })()`);
  const tools=[['read','bash'],['read'],['bash'],['mcp__fixture_inspector__inspect'],['mcp__fixture_inspector__inspect']];
  for(const [index,item] of result.entries()){
    assert.equal(item.accessed,index===0?2:1);assert.deepEqual(item.tools,tools[index]);assert.equal(item.visibleRows,tools[index].length);
  }
  assert.ok(result[0].visibleLogs.includes('Service fixture output'));assert.ok(result[2].visibleLogs.includes('Service fixture output'));
  assert.ok(result[3].visibleLogs.includes('MCP fixture output'));assert.ok(result[4].visibleLogs.includes('MCP fixture output'));
});

test('DEMO fades delivered arrows continuously during redraws instead of flashing them to the history opacity', {timeout:15000}, async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const result=await evaluate(`(async()=>{
    const nativeTimeout=window.setTimeout,timers=new Map();let token=-1;
    window.setTimeout=(fn,delay,...args)=>{const id=token--;timers.set(id,{fn,delay,args});return id};
    try{startDemo()}finally{window.setTimeout=nativeTimeout}
    const nextTask=demoTimers.map(id=>timers.get(id)).sort((a,b)=>a.delay-b.delay)[0];
    const samples=[],deadline=performance.now()+10000;
    let fading=false,queued=false;
    while(performance.now()<deadline){
      await new Promise(requestAnimationFrame);
      const context=visibleRouteContext();
      if(!queued&&context?.kind==='request'&&routePlayback.finished){queued=true;nextTask.fn(...nextTask.args)}
      if(context?.dimming){
        fading=true;
        const paths=[...svg.querySelectorAll('g[data-edge]')].filter(group=>group.dataset.stage===context.key).map(group=>group.querySelector('.edge'));
        samples.push(paths.map(path=>Number(getComputedStyle(path).opacity)));
        renderEdges();
      }else if(fading)break;
    }
    const diagnostic={context:visibleRouteContext(),hidden:document.hidden,paused:routePlayback.paused,moving:world.classList.contains('camera-moving'),finished:routePlayback.finished,transfers:[...wireEntries.values()].map(entry=>({phase:entry.phase,animations:entry.animations.map(animation=>({state:animation.playState,time:animation.currentTime}))}))};
    showLive();
    return{samples,diagnostic};
  })()`);
  assert.ok(result.samples.length>=4,JSON.stringify(result));
  assert.ok(result.samples.at(-1).length>0&&result.samples.at(-1).every((opacity,index)=>opacity<=.35),'Delivered paths must fade after arrival');
  for(let i=1;i<result.samples.length;i++)for(let j=0;j<result.samples[i].length;j++){
    assert.ok(result.samples[i][j]<=result.samples[i-1][j]+.03,'A fading arrow must not brighten again on redraw');
  }
});

test('Automatic card glow survives background snapshots and input-to-output handoff without restarting', {timeout:12000}, async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const result=await evaluate(`(async()=>{
    const until=async predicate=>{const deadline=performance.now()+8000;while(!predicate()&&performance.now()<deadline)await new Promise(requestAnimationFrame);if(!predicate())throw new Error('Expected automatic card transition did not arrive')};
    const command=commandTemplateFor('glob'),snapshot={run_id:'demo-focus-continuity',orchestrator_phase:'running',nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),edges:[{from:'agent-hub',to:command.id}],timestamp:'2026-01-01T00:00:00Z',event:{type:'TOOL_CALL_START',tool:'glob',call_id:'focus-call',input:JSON.stringify({pattern:'docs/*.md'})}};
    viewMode='demo';viewRunId=snapshot.run_id;steps=[snapshot];showStep(0);
    const card=document.getElementById(command.id);
    await until(()=>card.classList.contains('sequence-focus')&&(completionAnimations.get(card)?.currentTime||0)>400);
    const glow=completionAnimations.get(card),before=glow.currentTime;
    steps.push({...snapshot,timestamp:'2026-01-01T00:00:01Z',event:{type:'THINKING',phase:'Background metadata'}});showStep(1);
    await ${nextFrames};
    const background={same:completionAnimations.get(card)===glow,running:glow.playState==='running',advanced:glow.currentTime>before,focused:card.classList.contains('sequence-focus')};
    steps.push({...snapshot,timestamp:'2026-01-01T00:00:02Z',event:{type:'TOOL_CALL_END',tool:'glob',call_id:'focus-call',success:true,result:'Fixture matches'}});showStep(2);
    await until(()=>Boolean(visibleRouteContext()?.dimming));
    await ${nextFrames};
    const handoff={same:completionAnimations.get(card)===glow,running:glow.playState==='running'};
    showLive();return{background,handoff};
  })()`);
  assert.deepEqual(result.background,{same:true,running:true,advanced:true,focused:true});
  assert.deepEqual(result.handoff,{same:true,running:true});
});

test('Recorded CAD routing avoids an unnecessary staircase beside a shared source lane', async () => {
  await preparePage();
  const samples=await evaluate(`(()=>{
    const samples=[];
    for(const mirrored of [false,true])for(const reversed of [false,true]){
      const y=value=>mirrored?1500-value:value,rect=(key,left,right,top,bottom)=>({el:{dataset:{group:key}},left,right,top:Math.min(y(top),y(bottom)),bottom:Math.max(y(top),y(bottom)),cx:(left+right)/2,cy:y((top+bottom)/2)});
      const first=rect('agent',495,775,260,753),second=rect('memory',2200,2580,656,1033),docs=rect('documents',800,1360,36,676),groups=[first,docs,second],obstacles=groups.map(group=>({group:group.el,left:group.left-7,right:group.right+7,top:group.top-7,bottom:group.bottom+7}));
      const joins=[{x:481,y:y(412),key:'agent'},{x:447,y:y(412),key:'agent'}],reserved=[{a:{x:447,y:y(412)},b:{x:447,y:y(1230)},active:true,key:'agent~files',joins},{a:{x:431,y:y(412)},b:{x:431,y:y(29)},active:false,key:'agent~capabilities',joins}];
      const source=reversed?second:first,target=reversed?first:second,from={cx:source.cx,cy:y(reversed?871:412)},to={cx:target.cx,cy:y(reversed?412:871)},route=chooseGroupRoute(source,target,from,to,obstacles,7,reserved,{active:false});
      if(!route)throw new Error('A clear rail must remain routable');
      samples.push({mirrored,reversed,bends:route.points.length-2,hitsBlock:wireSegments(route.points).some(segment=>obstacles.some(block=>wireHitsRect(segment.a,segment.b,block))),points:route.points});
    }
    return samples;
  })()`);
  for(const sample of samples){assert.equal(sample.hitsBlock,false);assert.equal(sample.bends,2,JSON.stringify(sample))}
});

test('Growing working set keeps a fixed block, flat cards, scroll and per-file details', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    viewMode='saved';viewRunId='working-set-growth';
    const initial={run_id:viewRunId,prompt:'Read source files',orchestrator_phase:'running',nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),edges:[],event:{type:'PROMPT_START'}};
    steps=[initial];const sizes=[];
    for(let index=0;index<40;index++){
      steps.push({...structuredClone(initial),event:{type:'TOOL_CALL_START',tool:'read',call_id:'file-'+index,input:{path:'src/file_'+index+'.py'}}});
      showStep(steps.length-1);await new Promise(requestAnimationFrame);
      if(index===0||index===39)sizes.push(document.querySelector('.g-files').offsetHeight);
    }
    const body=document.querySelector('#files-container'),cards=body.querySelectorAll(':scope > .card');
    cards[0].click();
    const result={sizes,count:cards.length,scrollable:body.scrollHeight>body.clientHeight,categories:body.querySelectorAll('details').length,opened:document.querySelector('#resource-title').textContent};
    closeResourceDetails();return result;
  })()`);
  assert.equal(result.sizes[0],result.sizes[1]);assert.equal(result.count,40);assert.equal(result.scrollable,true);assert.equal(result.categories,0);assert.equal(result.opened,'src/file_0.py');
});

test('Tool efficiency never compares partial baselines with a larger output total', async () => {
  await preparePage();
  const result=await evaluate(`(()=>{
    const node={id:'integration_rtk',name:'rtk'},call=(id,output,without)=>({ordinal:id,ev:{call_id:String(id)},endEvent:{token_metrics:{output_tokens:output,...(without==null?{}:{without_tokens:without}),estimated:true,source:'fixture'}}});
    const partial=ObserverToolInsights.summary(node,[call(1,20,100),call(2,30)]);
    const full=ObserverToolInsights.summary(node,[call(1,20,100),call(2,30,200)]);
    const unknown=ObserverToolInsights.summary(node,[{ev:{},endEvent:{result:'captured text'}}]);
    return {partial,full,unknown};
  })()`);
  assert.equal(result.partial.without,null);assert.equal(result.partial.partial,true);assert.equal(result.full.without,300);assert.equal(result.full.output,50);assert.equal(result.unknown.without,null);assert.equal(result.unknown.estimated,true);
});

test('Expanded tool execution survives detail rebuilds and retains its recorded output', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const holder=document.createElement('section');document.body.append(holder);
    const node={id:'mcp_demo'},calls=[{ordinal:1,startedAt:'2026-01-01T00:00:00Z',tool:'inspect',ev:{call_id:'one',input:{path:'part.stl'}},endStep:2,endEvent:{result:'Actual recorded result'}}];
    toolInsights.renderDetails(holder,node,calls);holder.querySelector('details').open=true;await new Promise(resolve=>setTimeout(resolve,0));
    holder.replaceChildren();toolInsights.renderDetails(holder,node,calls);
    const result={open:holder.querySelector('details').open,text:holder.querySelector('details').textContent};holder.remove();return result;
  })()`);
  assert.equal(result.open,true);assert.ok(result.text.includes('Actual recorded result'));assert.ok(result.text.includes('part.stl'));
});

test('Memory cards show the configured service address rather than a stale historical endpoint', async () => {
  await preparePage();
  const result=await evaluate(`(()=>{
    const node=config.nodes.find(node=>node.kind==='memory');
    config.memory_systems=[{id:node.memory_system_id,name:'Hindsight · OMP',provider:'hindsight',endpoint:'http://127.0.0.1:18789',bank_ids:['omp'],categories:[],stores:[]}];
    state.nodes[node.id]={...node,endpoint:'http://127.0.0.1:8889',status:'idle'};
    memoryView.setCatalog(false);
    const card=document.getElementById(node.id),first=card.querySelector('.memory-endpoint code').textContent,firstTitle=card.title;
    config.memory_systems[0].endpoint='http://127.0.0.1:28789';memoryView.setCatalog(false);
    const holder=document.createElement('div');memoryView.renderInfo(holder,state.nodes[node.id]);
    return {first,firstTitle,current:card.querySelector('.memory-endpoint code').textContent,info:holder.querySelector('.memory-overview code').textContent,status:state.nodes[node.id].status};
  })()`);
  assert.equal(result.first,'http://127.0.0.1:18789');assert.ok(result.firstTitle.includes(result.first));assert.ok(!result.firstTitle.includes('8889'));
  assert.equal(result.current,'http://127.0.0.1:28789');assert.equal(result.info,result.current);assert.equal(result.status,'idle');
});

test('Terminal keeps the graph camera, renders literal logs and respects closed service tabs', async () => {
  localServices = [{ id: 'browser-fixture', name: 'External fixture', endpoint: 'http://127.0.0.1:1', endpoints: ['http://127.0.0.1:1'], status: 'running', connected: true, owner: 'external', can_start: false, can_stop: false, log_available: true }];
  try {
    await preparePage();
    const result = await evaluate(`(async()=>{
      await serviceTerminal.refresh();
      $('zoom-in').click();
      const before=[scale,panX,panY],frame=()=>new Promise(requestAnimationFrame);
      serviceTerminal.openService('browser-fixture');
      await frame();await frame();
      document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'0',bubbles:true}));
      for(let i=0;i<100&&serviceTerminal.inFlightLogsFetch;i++)await new Promise(resolve=>setTimeout(resolve,10));
      const pane=document.querySelector('.terminal-service-logs'),text=pane.textContent;
      const terminal={visible:document.body.classList.contains('terminal-active'),graphHidden:getComputedStyle(viewport).visibility==='hidden',callTrackHidden:getComputedStyle($('call-sequence')).display==='none',imageCount:pane.querySelectorAll('img').length,controlsVisible:[...document.querySelectorAll('.canvas-tools,.canvas-controls')].every(el=>getComputedStyle(el).visibility!=='hidden'&&getComputedStyle(el).display!=='none'),controlsDisabled:[...document.querySelectorAll('#activity-rail button')].every(button=>button.disabled),hiddenPanels:[...document.querySelectorAll('#terminal-view [hidden]')].every(el=>getComputedStyle(el).display==='none')};
      document.querySelector('#terminal-subtab-browser-fixture .tab-close').click();
      await serviceTerminal.refresh();
      const closed=document.querySelectorAll('.terminal-subtab').length===0;
      $('terminal-close').click();await frame();await frame();
      return {before,after:[scale,panX,panY],text,terminal,closed,stillRunning:serviceTerminal.services[0].status};
    })()`);
    assert.deepEqual(result.after, result.before);
    assert.equal(result.terminal.visible, true); assert.equal(result.terminal.graphHidden, true); assert.equal(result.terminal.callTrackHidden, true);
    assert.equal(result.terminal.imageCount, 0); assert.ok(result.text.includes('<img src=x onerror=alert(1)>'));
    assert.equal(result.terminal.controlsVisible,true);assert.equal(result.terminal.controlsDisabled,true);assert.equal(result.terminal.hiddenPanels,true);
    assert.equal(result.closed, true); assert.equal(result.stillRunning, 'running');
  } finally { localServices = []; }
});

test('Service shortcuts show host and launch hints, open log tabs and never restart running or pending services', async () => {
  localServices=[
    {id:'browser-fixture',name:'Hindsight fixture',description:'Local memory service',endpoint:'http://127.0.0.1:1',endpoints:['http://127.0.0.1:1'],status:'running',connected:true,owner:'external',can_start:false,launch_command:'python memory/runtime.py serve',cwd:'/observer-demo-regression',log_available:true},
    {id:'shortcut-worker',name:'Worker fixture',endpoint:'http://127.0.0.1:2',endpoints:['http://127.0.0.1:2'],status:'stopped',connected:false,owner:'none',can_start:true,launch_command:'npm run dev -- --port 2',cwd:'/observer-demo-regression'}
  ];
  try {
    await preparePage();
    const start=requests.length;
    const result=await evaluate(`(async()=>{
      await serviceTerminal.refresh();serviceTerminal.openService('browser-fixture');
      const shortcut=id=>document.querySelector('.terminal-service-shortcut[data-service-id="'+id+'"]');
      const hint=shortcut('shortcut-worker').dataset.tooltip;
      shortcut('browser-fixture').click();
      await new Promise(requestAnimationFrame);
      const running={tabs:serviceTerminal.openTabs.slice(),message:document.querySelector('.feedback-text')?.textContent};
      const native=serviceTerminal.request;let complete,starts=0;
      serviceTerminal.request=(url,...args)=>url.endsWith('/shortcut-worker/start')?(starts++,new Promise(resolve=>complete=resolve)):native(url,...args);
      try{
        shortcut('shortcut-worker').click();shortcut('shortcut-worker').click();
        const pending={starts,open:serviceTerminal.activeId,tabs:serviceTerminal.openTabs.slice()};
        complete({service:{...serviceTerminal.services.find(item=>item.id==='shortcut-worker'),status:'running',connected:true,can_start:false}});
        const deadline=performance.now()+2000;while(serviceTerminal.pendingAction.size&&performance.now()<deadline)await new Promise(requestAnimationFrame);
        ObserverI18n.setLanguage('ru');await new Promise(requestAnimationFrame);
        shortcut('browser-fixture').click();await new Promise(requestAnimationFrame);
        const russian={name:shortcut('browser-fixture').textContent,hint:shortcut('browser-fixture').dataset.tooltip,message:document.querySelector('.feedback-text')?.textContent};
        const bounds=[];
        for(const theme of ['light','dark']){document.documentElement.dataset.theme=theme;await new Promise(requestAnimationFrame);const row=document.querySelector('.terminal-tabs-row').getBoundingClientRect();bounds.push([...document.querySelectorAll('.terminal-service-shortcut')].every(button=>{const b=button.getBoundingClientRect();return b.left>=row.left&&b.right<=row.right&&b.top>=row.top&&b.bottom<=row.bottom}))}
        return{hint,running,pending,russian,bounds,selects:document.querySelectorAll('#terminal-view select').length,shortcutRole:document.querySelector('.terminal-service-shortcuts').getAttribute('role')};
      }finally{serviceTerminal.request=native;ObserverI18n.setLanguage('en')}
    })()`);
    assert.ok(result.hint.includes('Host: http://127.0.0.1:2'));assert.match(result.hint,/Launch: npm run dev/);assert.ok(result.hint.includes('Folder: /observer-demo-regression'));
    assert.deepEqual(result.running.tabs,['browser-fixture']);assert.match(result.running.message,/already running/);
    assert.deepEqual(result.pending,{starts:1,open:'shortcut-worker',tabs:['browser-fixture','shortcut-worker']});
    assert.equal(result.russian.name,'Hindsight fixture');assert.match(result.russian.hint,/Хост: http/);assert.match(result.russian.hint,/Запуск: python/);assert.match(result.russian.message,/уже запущен/);
    assert.ok(result.bounds.every(Boolean));assert.equal(result.selects,0);assert.equal(result.shortcutRole,'group');
    assert.equal(requests.slice(start).filter(row=>row.method==='POST'&&row.url.endsWith('/start')).length,0);
  } finally {localServices=[];}
});


test('Compact recorded routes restore runtime-only MCP connection changes without inventing usage or leaking into another route', async () => {
  await preparePage();
  const result = await evaluate(`(() => {
    const snapshot = {...structuredClone(state), nodes: {}, capability_inventory: {
      mcp: [{id: 'mcp_runtime_only', name: 'runtime-only', connection: 'connected', availability: 'connected'}],
      skills: [], integrations: [], native_tools: []
    }};
    renderState(snapshot);
    const card = document.getElementById('mcp_runtime_only');
    const connected = card.querySelector('.capability-state').textContent;
    const usage = state.nodes.mcp_runtime_only.usage_count;
    const observed = card.querySelector('.card-status').textContent;
    snapshot.capability_inventory.mcp[0].connection = 'disconnected';
    snapshot.capability_inventory.mcp[0].availability = 'disconnected';
    renderState(snapshot);
    const disconnected = document.getElementById('mcp_runtime_only').querySelector('.capability-state').textContent;
    renderState({...snapshot, capability_inventory: {mcp: [], skills: [], integrations: [], native_tools: []}});
    return {connected, disconnected, usage, observed, removed: !document.getElementById('mcp_runtime_only')};
  })()`);
  assert.match(result.connected, /connected/);
  assert.match(result.disconnected, /disconnected/);
  assert.equal(result.usage, 0);
  assert.equal(result.observed, 'Not observed');
  assert.equal(result.removed, true);
});

test('Hosted search details show parameters revealed at completion and retain the actual search output', async () => {
  await preparePage();
  const result = await evaluate(`(() => {
    viewMode = 'saved'; viewRunId = 'codex-hosted-search';
    const command = {id: 'command_hosted', name: 'WebSearch', label: 'WebSearch', kind: 'command', group: 'commands', status: 'idle', match: {tool_names: ['WebSearch']}};
    const initial = {run_id: viewRunId, context: {application: 'Codex'}, prompt: 'Find pathlib documentation', orchestrator_phase: 'complete', nodes: {[command.id]: command}, edges: []};
    const call = {tool: 'WebSearch', native_tool: true, call_id: 'search-late-parameters'};
    steps = [
      {...initial, event: {type: 'PROMPT_START'}},
      {...initial, event: {type: 'TOOL_CALL_START', ...call, input: JSON.stringify({query: '', action: null})}},
      {...initial, event: {type: 'TOOL_CALL_END', ...call, success: true, input: JSON.stringify({query: 'site:docs.python.org pathlib'}), result: JSON.stringify({results: [{url: 'https://docs.python.org/3/library/pathlib.html', title: 'pathlib documentation'}]})}}
    ];
    showStep(2);
    const card = [...document.querySelectorAll('#commands-container>.card')].find(card => card.querySelector('.card-title')?.textContent.toLowerCase() === 'websearch');
    if (!card) throw new Error('Hosted search command was not rendered');
    card.click();
    const content = document.querySelector('#resource-content').textContent;
    closeResourceDetails();
    return content;
  })()`);
  assert.match(result, /site:docs.python.org pathlib/);
  assert.ok(result.includes('https://docs.python.org/3/library/pathlib.html'));
});

test('Connecting an agent starts with its live inventory, not another runtime retained in history', async () => {
  await preparePage();
  const result = await evaluate(`(() => {
    const inventory = {mcp: [], skills: [], integrations: [], native_tools: []};
    const idle = {nodes: {}, edges: [], context: {}, orchestrator_phase: 'idle', capability_inventory: inventory};
    const native = {name: 'exec', description: 'Execute a Codex program'};
    const node = {id: 'command_codex_exec', kind: 'command', label: 'exec', status: 'hit'};
    const snapshot = {run_id: 'previous-codex', context: {application: 'Codex'}, prompt: 'Previous Codex request',
      orchestrator_phase: 'complete', nodes: {[node.id]: node}, edges: [],
      capability_inventory: {...inventory, source: 'codex-runtime', native_tools: [native]}};
    const saved = {run_id: snapshot.run_id, prompt: snapshot.prompt, status: 'completed',
      started_at: '2026-11-05T09:00:00Z', steps: [
        {...snapshot, event: {type: 'TOOL_CALL_START', tool: 'exec', native_tool: true, call_id: 'previous-call', input: '{"command":"pwd"}'}},
        {...snapshot, event: {type: 'TOOL_CALL_END', tool: 'exec', native_tool: true, call_id: 'previous-call', success: true, result: '/previous/project'}}
      ]};
    const before = JSON.stringify(saved);
    initialized = false;
    acceptInitial({config: {...config, agent_runtime: 'cad', native_tools: [], capability_inventory: inventory},
      state: idle, history: [], runs: [saved]});
    const labels = () => [...document.querySelectorAll('#commands-container>.card .card-title')].map(n => n.textContent);
    const initial = {mode: viewMode, commands: labels()};
    if (!blockSettings.dialog) blockSettings.build();
    blockSettings.group = 'commands';
    blockSettings.model = {hidden_commands: [], block_layouts: {commands: 'list'}};
    blockSettings.render();
    const choices = [...document.querySelectorAll('.block-command-option input')].map(n => n.getAttribute('aria-label'));
    showRun(saved, true);
    const historical = {mode: viewMode, commands: labels(), calls: buildCallSequence(steps).length, output: steps.at(-1).event.result};
    connections.onConfigChanged(config);
    return {initial, choices, historical, switched: {mode: viewMode, commands: labels()},
      retained: runs.some(run => run.run_id === saved.run_id), unchanged: JSON.stringify(saved) === before};
  })()`);
  assert.equal(result.initial.mode, 'live');
  assert.deepEqual(result.initial.commands, []);
  assert.deepEqual(result.choices, [], 'Undeclared Codex/OMP operations must not be offered for a CAD connection');
  assert.equal(result.historical.mode, 'saved');
  assert.deepEqual(result.historical.commands, ['exec']);
  assert.equal(result.historical.calls, 1);
  assert.equal(result.historical.output, '/previous/project');
  assert.equal(result.switched.mode, 'live');
  assert.deepEqual(result.switched.commands, []);
  assert.equal(result.retained, true);
  assert.equal(result.unchanged, true);
});

test('Declared Codex commands are visible before calls and retain distinct identities when one is used', async () => {
  await preparePage();
  const result = await evaluate(`(() => {
    const names = ['exec', 'exec_command', 'apply_patch', 'send_message', 'followup_task', 'interrupt_agent', 'clock.sleep'];
    const inventory = {mcp: [], skills: [], integrations: [], native_tools: names.map(name => ({name, description: 'Declared native operation', source: 'codex-session-schema'}))};
    const idle = {nodes: {}, edges: [], context: {}, orchestrator_phase: 'idle', capability_inventory: inventory};
    config = {...config, agent_runtime: 'codex', native_tools: inventory.native_tools, capability_inventory: inventory};
    renderCatalog(false); steps = []; currentStepIndex = -1; renderState(idle);
    const initial = Object.values(state.nodes).filter(isCommandNode).map(node => ({name: node.label, count: node.usage_count || 0}));
    const snapshot = {...idle, run_id: 'codex-distinct-tools', context: {application: 'Codex'}, orchestrator_phase: 'complete'};
    steps = [
      {...snapshot, event: {type: 'TOOL_CALL_START', tool: 'send_message', native_tool: true, call_id: 'message-one', input: '{"agent_id":"worker","message":"status"}'}},
      {...snapshot, event: {type: 'TOOL_CALL_END', tool: 'send_message', native_tool: true, call_id: 'message-one', success: true, result: 'Delivered'}}
    ];
    viewMode = 'saved'; viewRunId = snapshot.run_id; showStep(1);
    return {names, initial, after: Object.fromEntries(Object.values(state.nodes).filter(isCommandNode).map(node => [node.label, node.usage_count || 0])),
      calls: buildCallSequence(steps).length, cards: [...document.querySelectorAll('#commands-container>.card .card-title')].map(node => node.textContent)};
  })()`);
  assert.deepEqual(result.initial.map(node => node.name).sort(), result.names.sort());
  assert.ok(result.initial.every(node => node.count === 0), 'A declared command is not an invocation');
  assert.equal(result.calls, 1);
  assert.equal(result.after.send_message, 1);
  assert.equal(result.after.followup_task, 0, 'A follow-up task must not inherit another command’s call');
  assert.equal(result.after.interrupt_agent, 0);
  assert.deepEqual(result.cards.sort(), result.names);
});

async function waitForProject(root) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const result = await command('Runtime.evaluate', {
      expression: `typeof initialized !== 'undefined' && initialized && config.project_root === ${JSON.stringify(root)}`,
      returnByValue: true
    });
    if (result.result?.value === true) return;
    await pause(30);
  }
  throw new Error(`The graph did not open ${root}`);
}

test('Saved folders remain a preview through analysis until Generate graph; document views stay project-scoped', async () => {
  await preparePage();
  const preview = await evaluate(`(async()=>{
    applyDocumentsView('list');
    const button=document.querySelector('#current-project'),width=button.getBoundingClientRect().width,label=button.getAttribute('aria-label');
    await connections.open();
    [...document.querySelectorAll('.connection-workspace button')].find(button=>button.title===${JSON.stringify(alternateRoot)}).click();
    await new Promise(resolve=>{const check=()=>connections.busy?setTimeout(check,10):resolve();check()});
    return {open:connections.dialog.open,root:config.project_root,width,label};
  })()`);
  assert.equal(preview.open, true);
  assert.equal(preview.root, config.project_root);
  await evaluate(`(async()=>{await connections.act(()=>connections.analyze());if(config.project_root!==${JSON.stringify(config.project_root)})throw new Error('Analysis navigated before generation');await connections.act(()=>connections.generate())})()`);
  await waitForProject(alternateRoot);
  const result = await evaluate(`(()=>{
    applyDocumentsView('list');
    const list=[...document.querySelectorAll('#documents-container .doc-tree-file')].map(node=>node.dataset.nodeId).sort();
    applyDocumentsView('bubble');
    const bubbles=[...document.querySelectorAll('#document-bubbles .doc-bubble')].map(node=>node.dataset.nodeId).sort();
    return {list,bubbles,sharedPath:state.nodes[${JSON.stringify(sharedDocument.id)}].absolute_path,
      root:config.project_root,
      width:document.querySelector('#current-project').getBoundingClientRect().width,label:document.querySelector('#current-project').getAttribute('aria-label'),calls:buildCallSequence(steps).length};
  })()`);
  assert.deepEqual(result.list, alternateDocuments.map(node=>node.id).sort());
  assert.deepEqual(result.bubbles, result.list);
  assert.equal(result.sharedPath, `${alternateRoot}/${sharedDocument.path}`);
  assert.equal(result.root, alternateRoot);
  assert.equal(result.width, preview.width, 'Project names must not resize the header control');
  assert.equal(result.label, preview.label);
  assert.equal(result.calls, 0);
  await evaluate(`(async()=>{
    await connections.open();
    [...document.querySelectorAll('.connection-workspace button')].find(button=>button.title===${JSON.stringify(config.project_root)}).click();
    await new Promise(resolve=>{const check=()=>connections.busy?setTimeout(check,10):resolve();check()});
    await connections.act(()=>connections.analyze());await connections.act(()=>connections.generate());
  })()`);
  await waitForProject(config.project_root);
  const returned = await evaluate(`({mode:documentViewMode,documents:documentBubbleView.nodes.size,
    root:config.project_root})`);
  assert.equal(returned.mode, 'list', 'Another project must not replace this project’s document view preference');
  assert.equal(returned.documents, nodes.filter(node=>node.kind==='document').length);
  assert.equal(returned.root, config.project_root);
});

test('Typed paths and native folder selection keep settings open until explicit activation and retain observation state', async () => {
  await preparePage();
  const typed = await evaluate(`(async()=>{
    await connections.open();
    const input=document.querySelector('#connection-project-path');
    input.value=${JSON.stringify(alternateRoot)};input.dispatchEvent(new Event('input',{bubbles:true}));
    await connections.choosePath();
    return {open:connections.dialog.open,root:config.project_root,selected:connections.data.workspace.path};
  })()`);
  assert.equal(typed.open, true);
  assert.equal(typed.root, config.project_root);
  assert.equal(typed.selected, alternateRoot);
  await evaluate(`(async()=>{await connections.act(()=>connections.analyze());await connections.act(()=>connections.generate())})()`);
  await waitForProject(alternateRoot);
  const selected = await evaluate(`(async()=>{
    await connections.open();
    const enabled=connections.data.workspace.enabled;
    await connections.browse();
    return {enabled,open:connections.dialog.open,root:config.project_root,selected:connections.data.workspace.path};
  })()`);
  assert.equal(selected.enabled, false, 'Selecting a folder alone does not activate observation');
  assert.equal(selected.open, true);
  assert.equal(selected.root, alternateRoot);
  assert.equal(selected.selected, config.project_root);
  await evaluate(`(async()=>{await connections.act(()=>connections.analyze());await connections.act(()=>connections.generate())})()`);
  await waitForProject(config.project_root);
  const returned = await evaluate(`({documents:documentBubbleView.nodes.size,
    root:config.project_root,calls:buildCallSequence(steps).length})`);
  assert.equal(returned.documents, nodes.filter(node=>node.kind==='document').length);
  assert.equal(returned.root, config.project_root);
  assert.equal(returned.calls, 0);
});

test('Embedded CAD and Codex replace live commands and call history on both HTTP and WebSocket module cutovers', async () => {
  await preparePage();
  const result = await evaluate(`(()=>{
    const inventory=names=>({mcp:[],skills:[],integrations:[],native_tools:names.map(name=>({name,description:'Declared runtime operation'}))});
    const codex=inventory(['exec','apply_patch']),cad=inventory(['read','write','bash','subagent']);
    const codexConfig={...config,agent_runtime:'codex',native_tools:codex.native_tools,capability_inventory:codex};
    const cadConfig={...config,agent_runtime:'cad',native_tools:cad.native_tools,capability_inventory:cad};
    const recorded={nodes:{},edges:[],run_id:'codex-live-cutover',context:{application:'Codex'},orchestrator_phase:'complete',capability_inventory:codex};
    const history=[
      {...recorded,event:{type:'TOOL_CALL_START',tool:'exec',native_tool:true,call_id:'codex-before-cutover',input:'{"command":"printf result"}'}},
      {...recorded,event:{type:'TOOL_CALL_END',tool:'exec',native_tool:true,call_id:'codex-before-cutover',success:true,result:'result'}}
    ];
    const idle={nodes:{},edges:[],context:{application:'CAD Studio'},orchestrator_phase:'idle',capability_inventory:cad};
    const observe=()=>({names:[...document.querySelectorAll('#commands-container>.card .card-title')].map(node=>node.textContent).sort(),calls:buildCallSequence(steps).length,mode:viewMode});
    applyConnectionConfig(codexConfig,recorded,history);
    const before=observe();
    connections.onConfigChanged(cadConfig,idle,[]);
    const http=observe();
    onWsMessage({type:'RESET',module_changed:true,config:codexConfig,state:recorded,history});
    const returned=observe();
    onWsMessage({type:'RESET',module_changed:true,config:cadConfig,state:idle,history:[]});
    return {before,http,returned,websocket:observe()};
  })()`);
  assert.deepEqual(result.before.names, ['apply_patch','exec']);
  assert.equal(result.before.calls, 1);
  assert.deepEqual(result.http.names, ['bash','read','subagent','write']);
  assert.equal(result.http.calls, 0);
  assert.equal(result.http.mode, 'live');
  assert.deepEqual(result.returned.names, ['apply_patch','exec']);
  assert.equal(result.returned.calls, 1);
  assert.deepEqual(result.websocket.names, result.http.names);
  assert.equal(result.websocket.calls, 0);
});

test('A wire changes direction without keeping the old arrow and stays thin at every zoom', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))),read=commandTemplateFor('read');
    viewMode='saved';viewRunId='wire-direction';setRouteScope('current');
    const base={run_id:viewRunId,prompt:'Read twice',orchestrator_phase:'running',nodes:{[read.id]:{...read,status:'active'}},edges:[{from:'agent-hub',to:read.id}]};
    steps=[
      {...base,timestamp:'2026-01-01T00:00:00Z',event:{type:'PROMPT_START'}},
      {...base,timestamp:'2026-01-01T00:00:01Z',event:{type:'TOOL_CALL_START',tool:'read',call_id:'a',input:{path:'source.txt'}}},
      {...base,timestamp:'2026-01-01T00:00:02Z',event:{type:'TOOL_CALL_END',tool:'read',call_id:'a',success:true,result:'source'}},
      {...base,timestamp:'2026-01-01T00:00:03Z',event:{type:'TOOL_CALL_START',tool:'read',call_id:'b',input:{path:'source.txt'}}}
    ];
    const arrows=[];
    for(const index of [1,2,3]){showStep(index);await frame();const group=svg.querySelector('g[data-edge="agent~commands"]'),path=group.querySelector('.edge');arrows.push({direction:group.dataset.direction,start:path.hasAttribute('marker-start'),end:path.hasAttribute('marker-end'),trailMatches:group.querySelector('.edge-trail').getAttribute('d')===path.getAttribute('d')})}
    const widths=[];
    for(const zoom of [.15,.5,1,1.8]){scale=zoom;setWorld();await frame();const path=svg.querySelector('g[data-edge="agent~commands"] .edge');widths.push(parseFloat(getComputedStyle(path).strokeWidth)*zoom)}
    return {arrows,widths};
  })()`);
  assert.deepEqual(result.arrows.map(row=>[row.direction,row.start,row.end]),[
    ['forward',false,true],['return',true,false],['forward',false,true]
  ]);
  assert.ok(result.arrows.every(row=>row.trailMatches),'The faint route must not leave a second stale branch');
  assert.ok(result.widths.every(width=>Math.abs(width-1.7)<.02),'Visible width remains 1.7 CSS pixels across 15–180% zoom');
});

test('Concurrent project chats isolate calls, results and timeline selection while background events keep arriving', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const send=(session,run,index,type,extra={})=>{
      const context={session_id:session,session_name:'Session '+session,application:'Fixture agent'},snapshot={run_id:run,context,prompt:'Request '+session,nodes:{},edges:[],orchestrator_phase:type==='GENERATION_END'?'complete':'running',run_started_at:'2026-01-01T00:00:00Z'};
      onWsMessage({type:'STATE_UPDATE',session_id:session,state:snapshot,history_step:{...snapshot,step_index:index,timestamp:'2026-01-01T00:00:0'+index+'Z',event:{type,run_id:run,session_id:session,...extra}}});
    };
    send('alpha','run-alpha',0,'PROMPT_START');
    send('alpha','run-alpha',1,'TOOL_CALL_START',{tool:'read',call_id:'a',input:{path:'alpha.txt'}});
    send('beta','run-beta',0,'PROMPT_START');
    send('beta','run-beta',1,'TOOL_CALL_START',{tool:'bash',call_id:'b',input:{command:'printf beta'}});
    await new Promise((resolve,reject)=>{const start=performance.now();function check(){if(state?.prompt==='Request alpha'&&buildCallSequence(steps).length===1)resolve();else if(performance.now()-start>3000)reject(new Error('Selected session did not receive its live update'));else requestAnimationFrame(check)}check()});
    const initial={selected:selectedSessionId,prompt:state.prompt,calls:buildCallSequence(steps).map(call=>call.tool)};
    document.querySelector('[data-session="beta"]').click();await frame();
    const beta={selected:selectedSessionId,prompt:state.prompt,calls:buildCallSequence(steps).map(call=>call.tool)};
    showStep(0);
    send('alpha','run-alpha',2,'TOOL_CALL_END',{tool:'read',call_id:'a',success:true,result:'alpha only'});
    send('alpha','run-alpha',3,'GENERATION_END',{result:'Alpha done'});
    await frame();
    const held={selected:selectedSessionId,prompt:state.prompt,index:currentStepIndex,calls:buildCallSequence(steps).map(call=>call.tool)};
    document.querySelector('[data-session="alpha"]').click();await frame();
    const alpha={prompt:state.prompt,phase:state.orchestrator_phase,calls:buildCallSequence(steps).map(call=>call.tool),output:buildCallSequence(steps)[0]?.endEvent?.result};
    document.querySelector('[data-session="beta"]').click();await frame();
    const restored={selected:selectedSessionId,prompt:state.prompt,index:currentStepIndex,calls:buildCallSequence(steps).map(call=>call.tool)};
    return {initial,beta,held,alpha,restored,tabs:[...document.querySelectorAll('.session-chat')].map(button=>button.dataset.session)};
  })()`);
  assert.equal(result.initial.selected,'alpha');assert.equal(result.initial.prompt,'Request alpha');assert.deepEqual(result.initial.calls,['read']);
  assert.equal(result.beta.selected,'beta');assert.equal(result.beta.prompt,'Request beta');assert.deepEqual(result.beta.calls,['bash']);
  assert.equal(result.held.selected,'beta');assert.equal(result.held.index,0);assert.equal(result.held.prompt,'Request beta');
  assert.equal(result.alpha.prompt,'Request alpha');assert.equal(result.alpha.phase,'complete');assert.deepEqual(result.alpha.calls,['read']);assert.equal(result.alpha.output,'alpha only');
  assert.equal(result.restored.selected,'beta');assert.equal(result.restored.index,0);assert.deepEqual(result.restored.calls,['bash']);
  assert.deepEqual(result.tabs,['alpha','beta']);
});

test('Selecting a discovered idle session never displays another session request or calls', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const snapshot={run_id:'busy-run',prompt:'Private busy request',context:{session_id:'busy-session'},nodes:{},edges:[],orchestrator_phase:'running'};
    onWsMessage({type:'STATE_UPDATE',session_id:'busy-session',state:snapshot,history_step:{...snapshot,step_index:0,event:{type:'PROMPT_START',session_id:'busy-session'}}});
    mergeSessionList({sessions:[{session_id:'idle-session',session_name:'Waiting workspace',status:'unknown'}]},false);
    await selectSession('idle-session');
    return {selected:selectedSessionId,session:state.context.session_id,phase:state.orchestrator_phase,prompt:document.querySelector('#prompt-content').textContent,calls:buildCallSequence(steps).length,edges:state.edges.length};
  })()`);
  assert.equal(result.selected,'idle-session');assert.equal(result.session,'idle-session');assert.equal(result.phase,'idle');
  assert.ok(!result.prompt.includes('Private busy request'));assert.equal(result.calls,0);assert.equal(result.edges,0);
});

test('Folder role lists equal-sized agent cards and analysis enables only its own source before generation', async () => {
  await preparePage();
  const start=requests.length;
  const result=await evaluate(`(async()=>{
    await connections.open();
    const observe=()=>({module:[...document.querySelectorAll('.connection-module-top h3')].map(node=>node.textContent),disabled:document.querySelector('.connection-generate').disabled,root:config.project_root});
    const initial=observe();
    const cards=[...document.querySelectorAll('.connection-agent-list .connection-module')].map(card=>{
      const r=card.getBoundingClientRect(),repo=card.querySelector('.connection-repository'),generate=card.querySelector('.connection-generate').getBoundingClientRect(),link=repo.getBoundingClientRect();
      return {width:r.width,height:r.height,top:r.top,bottom:r.bottom,href:repo.getAttribute('href'),disabled:repo.getAttribute('aria-disabled'),opacity:parseFloat(getComputedStyle(repo).opacity),actionY:generate.top+generate.height/2,repoY:link.top+link.height/2};
    });
    document.querySelector('input[value="agent_system"]').click();const embedded=observe();
    document.querySelector('input[value="working_repository"]').click();
    await connections.act(()=>connections.analyze());const verified=observe();
    const report={checks:document.querySelector('.connection-checks').textContent,log:document.querySelector('.connection-analysis-log').textContent,scripts:document.querySelectorAll('.connection-analysis-log script').length};
    document.querySelector('[data-module="omp"] .connection-analyze').click();
    while(connections.busy)await new Promise(requestAnimationFrame);
    const sourceGate=[...document.querySelectorAll('.connection-module')].map(card=>({id:card.dataset.module,disabled:card.querySelector('.connection-generate').disabled}));
    document.querySelector('input[value="agent_system"]').click();const invalidated=observe();
    await connections.act(()=>connections.analyze());const blocked=observe();
    const error=document.querySelector('.connection-checks').textContent;
    return {initial,cards,sourceGate,embedded,verified,report,invalidated,blocked,error};
  })()`);
  assert.deepEqual(result.initial.module,['Codex Desktop / CLI','OMP — Oh My Pi']);assert.equal(result.initial.disabled,true);
  const [codex,omp]=result.cards;
  assert.ok(Math.abs(codex.height-omp.height)<1);assert.ok(Math.abs(codex.width-omp.width)<1);assert.ok(omp.top>=codex.bottom);
  for(const card of result.cards)assert.ok(Math.abs(card.actionY-card.repoY)<1);
  assert.equal(omp.href,'https://github.com/can1357/oh-my-pi');assert.equal(codex.href,null);assert.equal(codex.disabled,'true');assert.ok(codex.opacity<.5);
  assert.deepEqual(result.sourceGate,[{id:'codex',disabled:true},{id:'omp',disabled:false}]);
  assert.deepEqual(result.embedded.module,['Project agent']);assert.equal(result.embedded.disabled,true);
  assert.equal(result.verified.disabled,false);assert.equal(result.verified.root,config.project_root);
  assert.ok(result.report.log.includes('<script>untrusted text</script>'));assert.equal(result.report.scripts,0);
  assert.equal(result.invalidated.disabled,true);assert.equal(result.blocked.disabled,true);assert.ok(result.error.includes('Project telemetry integration is missing'));
  assert.equal(requests.slice(start).filter(request=>request.url==='/api/connections/connect'||request.url==='/api/connections/generate'||request.url.startsWith('/api/installations/start')).length,0);
});

test('Each agent owns a collapsible analysis panel and reanalysis replaces only its report', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const result=await evaluate(`(async()=>{
    await connections.open();
    const frame=()=>new Promise(requestAnimationFrame);
    const settle=async()=>{for(const panel of document.querySelectorAll('.connection-analysis-disclosure'))for(const a of panel.getAnimations())a.finish();await frame()};
    const card=id=>document.querySelector('.connection-module[data-module="'+id+'"]');
    const panel=id=>card(id).nextElementSibling;
    const analyze=async id=>{card(id).querySelector('.connection-analyze').click();while(connections.busy)await frame();await settle()};
    await analyze('codex');
    const codex=panel('codex').textContent;
    card('codex').querySelector('.connection-analysis-toggle').click();
    const animation=panel('codex').getAnimations()[0];
    animation.pause();animation.currentTime=130;
    const mid={height:panel('codex').getBoundingClientRect().height,full:panel('codex').scrollHeight};
    await settle();
    const collapsed={hidden:panel('codex').hidden,inert:panel('codex').inert,expanded:card('codex').querySelector('.connection-analysis-toggle').getAttribute('aria-expanded')};
    await analyze('omp');
    const isolated={codex:panel('codex').textContent===codex,hidden:panel('codex').hidden,omp:panel('omp').querySelector('.connection-checks').textContent,attached:panel('omp').parentElement===card('omp').parentElement};
    card('omp').querySelector('.connection-analysis-toggle').click();await settle();
    await analyze('omp');
    const refreshed={panels:document.querySelectorAll('#connection-analysis-omp').length,checks:panel('omp').querySelectorAll('.connection-analysis').length,hidden:panel('omp').hidden,otherHidden:panel('codex').hidden,other:panel('codex').textContent===codex};
    card('omp').querySelector('.connection-analysis-toggle').click();
    card('omp').querySelector('.connection-analysis-toggle').click();
    await settle();
    return {mid,collapsed,isolated,refreshed,reversed:!panel('omp').hidden,
      heights:[...document.querySelectorAll('.connection-module')].map(el=>el.getBoundingClientRect().height)};
  })()`);
  assert.ok(result.mid.height>0&&result.mid.height<result.mid.full);
  assert.deepEqual(result.collapsed,{hidden:true,inert:true,expanded:'false'});
  assert.equal(result.isolated.codex,true);assert.equal(result.isolated.hidden,true);assert.equal(result.isolated.attached,true);assert.ok(result.isolated.omp.includes('OMP journals'));
  assert.deepEqual(result.refreshed,{panels:1,checks:1,hidden:false,otherHidden:true,other:true});
  assert.equal(result.reversed,true);assert.ok(Math.abs(result.heights[0]-result.heights[1])<1);
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  const reduced=await evaluate(`(()=>{
    const card=document.querySelector('.connection-module[data-module="omp"]'),panel=card.nextElementSibling,toggle=card.querySelector('.connection-analysis-toggle');
    toggle.click();const hidden=panel.hidden;toggle.click();return {hidden,shown:!panel.hidden,animations:panel.getAnimations().length};
  })()`);
  assert.deepEqual(reduced,{hidden:true,shown:true,animations:0});
});

test('Folder analysis failure leaves the form retryable without enabling Generate graph', async () => {
  await preparePage();
  await evaluate('connections.open()');
  failFolderAnalysis=true;
  try {
    const failed=await evaluate(`(async()=>{await connections.act(()=>connections.analyze());return {error:document.querySelector('.connection-analysis-error').textContent,generate:document.querySelector('.connection-generate').disabled,analyze:document.querySelector('.connection-analyze').disabled,editable:!connections.pathInput.readOnly,open:connections.dialog.open}})()`);
    assert.ok(failed.error.includes('Session index cannot be read'));assert.equal(failed.generate,true);assert.equal(failed.analyze,false);assert.equal(failed.editable,true);assert.equal(failed.open,true);
  } finally {failFolderAnalysis=false;}
  const recovered=await evaluate(`(async()=>{await connections.act(()=>connections.analyze());return {enabled:!document.querySelector('.connection-generate').disabled,error:document.querySelector('.connection-analysis-error')?.textContent||'',root:config.project_root}})()`);
  assert.equal(recovered.enabled,true);assert.equal(recovered.error,'');assert.equal(recovered.root,config.project_root);
});

test('Stale generation proof is rejected visibly and requires reanalysis before graph activation', async () => {
  await preparePage();
  await evaluate('(async()=>{await connections.open();await connections.act(()=>connections.analyze())})()');
  failGraphGeneration=true;
  try {
    const stale=await evaluate(`(async()=>{await connections.act(()=>connections.generate());return {open:connections.dialog.open,disabled:document.querySelector('.connection-generate').disabled,error:document.querySelector('.connection-analysis-error').textContent,root:config.project_root}})()`);
    assert.equal(stale.open,true);assert.equal(stale.disabled,true);assert.ok(stale.error.includes('analyze again'));assert.equal(stale.root,config.project_root);
  } finally {failGraphGeneration=false;}
  const recovered=await evaluate(`(async()=>{await connections.act(()=>connections.analyze());await connections.act(()=>connections.generate());return {open:connections.dialog.open,root:config.project_root,mode:viewMode}})()`);
  assert.equal(recovered.open,false);assert.equal(recovered.root,config.project_root);assert.equal(recovered.mode,'live');
});


test('Session actions and cancelled renaming do not select another chat or write a name', async () => {
  await preparePage();
  const start=requests.length;
  const result=await evaluate(`(async()=>{
    mergeSessionList({sessions:[{session_id:'idle-session',session_name:'Current chat',application:'Codex'},{session_id:'other-chat',session_name:'Other chat',application:'Codex'}]},false);
    await selectSession('idle-session');
    const before=document.querySelector('.session-chat[data-session="other-chat"] .session-chat-label').textContent;
    document.querySelector('.session-chat-more[data-session="other-chat"]').click();
    const selectedWhileOpen=selectedSessionId;
    document.querySelector('.session-actions-popover button').click();
    document.querySelector('#session-rename-name').value='Cancelled name';
    document.querySelector('.session-rename-cancel').click();
    return {selectedWhileOpen,selected:selectedSessionId,closed:!sessionRenameDialog.open,
      unchanged:before===document.querySelector('.session-chat[data-session="other-chat"] .session-chat-label').textContent};
  })()`);
  assert.equal(result.selectedWhileOpen,'idle-session');assert.equal(result.selected,'idle-session');
  assert.equal(result.closed,true);assert.equal(result.unchanged,true);
  assert.equal(requests.slice(start).filter(request=>request.url==='/api/session-name').length,0);
});

test('Language selection survives reload and localizes new panels without changing recorded content or technical names', async () => {
  await preparePage();
  try {
    const result=await evaluate(`(async()=>{
      document.documentElement.setAttribute('translate','no');document.body.setAttribute('translate','no');
      const literal=document.createElement('span');literal.setAttribute('translate','no');literal.textContent='Settings';document.body.append(literal);
      document.querySelector('#language-toggle').click();
      const resource={id:'mcp_i18n',kind:'mcp',group:'capabilities',name:'Settings',label:'Settings',description:'Fixture MCP'};
      config.nodes.push(resource);renderCatalog(false);
      state.prompt='Save';requestSignature=null;renderRequestBlocks();
      mergeSessionList({sessions:[{session_id:'i18n-session',session_name:'Settings',application:'Codex',status:'idle'}]},false);
      select(resource.id,resource);
      await connections.open();
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const result={language:document.documentElement.lang,heading:document.querySelector('.session-sidebar-heading strong').textContent,
        prompt:document.querySelector('#prompt-content').textContent.trim(),resource:document.querySelector('#mcp_i18n .card-title').textContent,
        inspector:document.querySelector('#inspector-title').textContent,chat:document.querySelector('[data-session="i18n-session"] .session-chat-label').textContent,
        dialog:document.querySelector('#observer-connections-title').textContent,query:new URL(location.href).searchParams.get('lang'),literal:literal.textContent};
      connections.close();return result;
    })()`);
    assert.equal(result.language,'ru');assert.equal(result.query,'ru');
    assert.match(result.heading,/Сессии/i);assert.match(result.dialog,/Подключения/);
    assert.equal(result.prompt,'Save');assert.equal(result.resource,'Settings');assert.equal(result.inspector,'Settings');assert.equal(result.chat,'Settings');
    assert.equal(result.literal,'Settings');
    await preparePage();
    assert.equal(await evaluate('document.documentElement.lang'),'ru');
    const restored=await evaluate(`(async()=>{document.querySelector('#language-toggle').click();await new Promise(requestAnimationFrame);return {lang:document.documentElement.lang,heading:document.querySelector('.session-sidebar-heading strong').textContent}})()`);
    assert.equal(restored.lang,'en');assert.equal(restored.heading,'Sessions');
  } finally {
    await evaluate("ObserverI18n.setLanguage('en')");
  }
});

test('Localization distinguishes empty-result UI from an identical captured response and keeps interpolated data intact', async () => {
  await preparePage();
  try {
    const result=await evaluate(`(async()=>{
      ObserverI18n.setLanguage('ru');
      const raw=structuredClone(state);
      state={...state,prompt:'Waiting for an agent run.'};requestSignature=null;renderRequestBlocks();
      const recordedPrompt=document.querySelector('#prompt-content').textContent.trim();
      state={...state,prompt:''};requestSignature=null;renderRequestBlocks();
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const emptyPrompt=document.querySelector('#prompt-content').textContent;
      const card=document.querySelector('#run_result'),context={mode:'demo',status:'idle'};
      resultView.render(card,{text:'No result yet.',files:[]},context);
      await new Promise(requestAnimationFrame);
      const recordedResult=card.querySelector('.result-response-preview').textContent.trim();
      resultView.render(card,{text:'',files:[]},context);
      await new Promise(requestAnimationFrame);
      const emptyResult=card.querySelector('.result-response-preview').textContent;
      const counter=ObserverI18n.t('24 listed · 2 used · 3 calls');
      const actions=ObserverI18n.t('Session actions: Settings');
      ObserverI18n.setLanguage('en');
      const restored=document.querySelector('#prompt-content').textContent;
      state=raw;requestSignature=null;renderRequestBlocks();
      return {recordedPrompt,emptyPrompt,recordedResult,emptyResult,counter,actions,restored};
    })()`);
    assert.equal(result.recordedPrompt,'Waiting for an agent run.');
    assert.match(result.emptyPrompt,/[А-Яа-я]/);
    assert.equal(result.restored,'Waiting for an agent run.');
    assert.equal(result.recordedResult,'No result yet.');assert.match(result.emptyResult,/[А-Яа-я]/);
    assert.match(result.counter,/24.*2.*3/);assert.doesNotMatch(result.counter,/listed|used|calls/);
    assert.match(result.actions,/Действия.*Settings$/);
  } finally {
    await evaluate("ObserverI18n.setLanguage('en')");
  }
});

test('Folder settings show only the selected project and leave its expansion, current graph and session unchanged', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    await refreshSessionProjects();
    const group=[...document.querySelectorAll('.session-project')].find(el=>el.dataset.project==='${alternateRoot}');
    const open=group.open,before=JSON.stringify(config),selected=selectedSessionId,services=JSON.stringify(serviceTerminal.services);
    group.querySelector('.session-project-settings').click();
    const deadline=performance.now()+5000;
    while(!graphSettings.data&&performance.now()<deadline)await new Promise(requestAnimationFrame);
    const foreign={root:graphSettings.data?.project_root,names:[...graphSettings.list.querySelectorAll('strong')].map(el=>el.textContent),
      sameConfig:JSON.stringify(config)===before,sameSession:selected===selectedSessionId,sameServices:services===JSON.stringify(serviceTerminal.services),
      sameExpansion:open===group.open};
    graphSettings.updateSessionActivity({sessions:[{session_id:'wrong',project_path:config.project_root,status:'running'}]});
    foreign.sessions=graphSettings.sessionsData.sessions.map(row=>row.session_id);
    graphSettings.dialog.close();
    return foreign;
  })()`);
  assert.equal(result.root,alternateRoot);
  assert.deepEqual(result.names,['Foreign MCP']);
  assert.deepEqual(result.sessions,['foreign-session']);
  assert.equal(result.sameConfig,true);assert.equal(result.sameSession,true);assert.equal(result.sameServices,true);assert.equal(result.sameExpansion,true);
});

test('A late settings response cannot replace the newly selected folder or its canvas', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const original=graphSettings.request;let resolveOld;
    graphSettings.request=(endpoint,body)=>graphSettings.project.path==='${alternateRoot}'&&endpoint==='/api/graph-settings'
      ? new Promise(resolve=>{resolveOld=resolve}) : original(endpoint,body);
    try{
      graphSettings.open({path:'${alternateRoot}',name:'Other project',current:false});
      graphSettings.open({path:config.project_root,name:'Current project',current:true});
      const deadline=performance.now()+5000;
      while(!graphSettings.data&&performance.now()<deadline)await new Promise(requestAnimationFrame);
      resolveOld({project_root:'${alternateRoot}',config:{project_root:'${alternateRoot}',nodes:[]},rows:[{id:'stale',name:'Stale foreign MCP',kind:'mcp'}]});
      await new Promise(requestAnimationFrame);
      return {root:graphSettings.data?.project_root,canvas:config.project_root,names:[...graphSettings.list.querySelectorAll('strong')].map(el=>el.textContent)};
    }finally{graphSettings.request=original;graphSettings.dialog.close()}
  })()`);
  assert.equal(result.root,config.project_root);assert.equal(result.canvas,config.project_root);
  assert.equal(result.names.includes('Stale foreign MCP'),false);
});

test('Running sessions animate their source tag or fallback dot, including background chats, and stop on completion or reduced motion', async () => {
  await preparePage();
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const inspect=`['background','codex-working','omp:working'].map(id=>{
    const row=document.querySelector('.session-chat[data-session="'+id+'"]'),agent=row.querySelector('.session-chat-agent'),dot=row.querySelector('.session-chat-status');
    return {id,status:row.dataset.status,tag:agent.textContent,tagMotion:agent.getAnimations().filter(a=>a.playState==='running').length,
      dotMotion:dot.getAnimations({subtree:true}).filter(a=>a.playState==='running').length,dotWidth:dot.getBoundingClientRect().width};
  })`;
  const running=await evaluate(`(async()=>{
    mergeSessionList({sessions:[
      {session_id:'front',session_name:'Foreground',status:'idle'},
      {session_id:'background',session_name:'CAD background',application:'CAD',status:'idle'},
      {session_id:'codex-working',session_name:'Codex background',application:'Codex',status:'idle'},
      {session_id:'omp:working',session_name:'OMP background',application:'OMP',status:'idle'}
    ]},false);
    selectedSessionId='front';
    for(const id of ['background','codex-working','omp:working'])
      cacheSessionUpdate({session_id:id,state:{run_id:'activity',context:{session_id:id},orchestrator_phase:'running'}});
    await new Promise(requestAnimationFrame);
    return {selected:selectedSessionId,rows:${inspect},idle:document.querySelector('.session-chat[data-session="front"]').getAnimations({subtree:true}).length};
  })()`);
  assert.equal(running.selected,'front');assert.equal(running.idle,0);
  const [fallback,...tagged]=running.rows;
  assert.equal(fallback.status,'running');assert.equal(fallback.tag,'');assert.equal(fallback.tagMotion,0);assert.ok(fallback.dotMotion>0);assert.ok(fallback.dotWidth>=6);
  assert.deepEqual(tagged.map(row=>row.tag),['codex','OMP']);
  for(const row of tagged){assert.equal(row.status,'running');assert.ok(row.tagMotion>0);assert.equal(row.dotMotion,0);assert.ok(row.dotWidth<=1)}
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  const reduced=await evaluate(`(async()=>{await new Promise(requestAnimationFrame);return ${inspect}})()`);
  for(const row of reduced){assert.equal(row.status,'running');assert.equal(row.tagMotion,0);assert.equal(row.dotMotion,0)}
  await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
  const completed=await evaluate(`(async()=>{
    for(const id of ['background','codex-working','omp:working'])
      cacheSessionUpdate({session_id:id,state:{run_id:'activity',context:{session_id:id},orchestrator_phase:'complete'}});
    await new Promise(requestAnimationFrame);return ${inspect};
  })()`);
  for(const row of completed){assert.equal(row.status,'completed');assert.equal(row.tagMotion,0);assert.equal(row.dotMotion,0)}
});

test('Published responses and prompts render Markdown consistently without executing HTML or fetching remote images', async () => {
  await preparePage();
  const source='# Answer\n\n**Important** and *emphasis* and ~~old~~.\n\n- one\n- [x] checked\n- [ ] pending\n\n| Tool | Count |\n| --- | --- |\n| read | 2 |\n\n> Quoted note\n\n```js\nconst value = "<script>unsafe</script>";\n```\n\n[Docs](https://example.com/docs) ![private image](https://example.com/track.png)\n\n<script>window.__markdownUnsafe=true</script>\n\n[unsafe](javascript:alert(1))';
  const result=await evaluate(`(async()=>{
    const text=${JSON.stringify(source)},card=document.querySelector('#run_result');
    state={...state,prompt:text};requestSignature=null;renderRequestBlocks();
    resultView.render(card,{text,files:[]},{mode:'demo',status:'hit',runId:'markdown-report'});
    await resultView.open({text,files:[]},{mode:'demo',status:'hit',runId:'markdown-report'});
    const inspect=root=>({heading:root.querySelector('h1')?.textContent,strong:root.querySelector('strong')?.textContent,
      rows:root.querySelectorAll('table tbody tr').length,checked:root.querySelectorAll('input[type=checkbox]:checked').length,
      pending:root.querySelectorAll('input[type=checkbox]:not(:checked)').length,code:root.querySelector('pre code')?.textContent,
      image:root.querySelectorAll('img').length,script:root.querySelectorAll('script').length,
      unsafe:[...root.querySelectorAll('a')].some(link=>/^javascript:/i.test(link.getAttribute('href')||'')),
      link:root.querySelector('a[href="https://example.com/docs"]')?.rel});
    const result={prompt:inspect(document.querySelector('#prompt-content')),card:inspect(card),dialog:inspect(resultView.body),
      mutated:state.prompt!==text,executed:Boolean(window.__markdownUnsafe)};
    resultView.dialog.close();return result;
  })()`);
  for(const view of [result.prompt,result.card,result.dialog]){
    assert.equal(view.heading,'Answer');assert.equal(view.strong,'Important');
    assert.equal(view.rows,1);assert.equal(view.checked,1);assert.equal(view.pending,1);
    assert.match(view.code,/const value = "<script>unsafe<\/script>";/);
    assert.equal(view.image,0);assert.equal(view.script,0);assert.equal(view.unsafe,false);
    assert.match(view.link,/noopener/);
  }
  assert.equal(result.mutated,false);assert.equal(result.executed,false);
});

test('Execution reports count paired calls once, retain failures and missing completions, and exclude declared-only MCP on rewind', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    viewMode='saved';viewRunId='report-evidence';
    const base={run_id:viewRunId,prompt:'Inspect source',nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),edges:[],orchestrator_phase:'running'};
    const events=[{type:'PROMPT_START'},{type:'TOOL_CALL_START',tool:'read',call_id:'read-1',input:{path:'docs/standards/guide.md'}},
      {type:'TOOL_CALL_END',tool:'read',call_id:'read-1',success:true,result:'source'},
      {type:'TOOL_CALL_START',tool:'mcp__demo__inspect',call_id:'mcp-1',mcp_server:'demo',input:{}},
      {type:'TOOL_CALL_END',tool:'mcp__demo__inspect',call_id:'mcp-1',mcp_server:'demo',success:false,result:'Failure'},
      {type:'TOOL_CALL_START',tool:'read',call_id:'read-2',input:{path:'docs/geometry/guide.md'}}];
    steps=events.map((event,index)=>({...structuredClone(base),timestamp:new Date(1700000000000+index*1000).toISOString(),event,step_index:index}));
    showStep(steps.length-1);
    const report=structuredClone(routeInvocationCache.report);
    const files=[{id:'known',path:'src/main.js',name:'main.js',kind:'code',change:'modified',added:4,removed:2,before:'old',after:'new',diff:'--- Before\\n+++ After\\n-old\\n+new'},
      {id:'unknown',path:'src/unknown.js',name:'unknown.js',kind:'code',change:'modified',added:null,removed:null}];
    await resultView.open({text:'**Recorded result**',files},{mode:'demo',status:'hit',runId:viewRunId,report});
    const text=resultView.reportHolder.textContent,known=resultView.reportHolder.querySelectorAll('.result-added').length;
    resultView.dialog.close();showStep(2);const rewind=structuredClone(routeInvocationCache.report);
    return {report,rewind,text,known};
  })()`);
  assert.equal(result.report.total,3);assert.equal(result.report.failed,1);assert.equal(result.report.pending,1);
  assert.equal(result.report.rows.find(row=>row.kind==='command'&&row.name==='read')?.count,2);
  const mcp=result.report.rows.find(row=>row.kind==='mcp');
  assert.equal(mcp?.count,1);assert.equal(mcp?.failed,1);
  assert.equal(result.known,1);assert.match(result.text,/\+4/);assert.match(result.text,/−2/);assert.match(result.text,/Line counts not captured/);
  assert.equal(result.rewind.total,1);assert.equal(result.rewind.rows.some(row=>row.kind==='mcp'),false);
});

test('Long result responses use the full dialog scroll surface, while short reports do not reserve an empty screen', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const report={total:0,failed:0,pending:0,intervalMs:null,rows:[]},context={mode:'demo',runId:'long-response',status:'hit',report};
    await resultView.open({text:Array.from({length:40},(_,i)=>'Paragraph '+i+' — **visible content**.').join('\\n\\n'),files:[]},context);
    await new Promise(requestAnimationFrame);
    const text=resultView.body.querySelector('.result-full-text');
    const long={height:text.clientHeight,overflow:getComputedStyle(text).overflowY,scrolls:resultView.body.scrollHeight>resultView.body.clientHeight,
      bounded:resultView.dialog.getBoundingClientRect().height<=innerHeight-24};
    resultView.dialog.close();
    await resultView.open({text:'**Ready.**',files:[]},{...context,runId:'short-response'});
    await new Promise(requestAnimationFrame);
    const short=resultView.dialog.getBoundingClientRect().height;
    resultView.dialog.close();return{long,short,viewport:innerHeight};
  })()`);
  assert.ok(result.long.height>280);assert.equal(result.long.overflow,'visible');assert.equal(result.long.scrolls,true);assert.equal(result.long.bounded,true);
  assert.ok(result.short<result.viewport*.8);
});

test('Switching terminal folders rejects late stdout from the previous project even with identical service IDs', async () => {
  localServices=[{id:'browser-fixture',name:'Current service',status:'running',connected:true,owner:'external',log_available:true}];
  try {
    await preparePage();
    const result=await evaluate(`(async()=>{
      const original=serviceTerminal.request;let release;
      serviceTerminal.request=(endpoint,body)=>endpoint.includes('/logs')&&serviceTerminal.project.current
        ? original(endpoint,body).then(data=>new Promise(resolve=>{release=()=>resolve(data)})) : original(endpoint,body);
      serviceTerminal.openService('browser-fixture');
      while(!release)await new Promise(requestAnimationFrame);
      await serviceTerminal.setProject({path:'${alternateRoot}',name:'Other project',current:false},'foreign-session');
      serviceTerminal.openService('browser-fixture');
      const deadline=performance.now()+5000;
      while(!document.querySelector('.terminal-logs-container').textContent.includes('FOREIGN-PROJECT-OUTPUT')&&performance.now()<deadline)await new Promise(requestAnimationFrame);
      release();await ${nextFrames};
      return{logs:document.querySelector('.terminal-logs-container').textContent,context:document.querySelector('.terminal-context').textContent};
    })()`);
    assert.match(result.logs,/FOREIGN-PROJECT-OUTPUT/);
    assert.doesNotMatch(result.logs,/<img|service output/);
    assert.match(result.context,/Other project.*foreign-session/);
  } finally {localServices=[];}
});

test('Hiding optional blocks removes their connections and leaves a centered mandatory-only packed graph', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const command=commandTemplateFor('bash'),snapshot={run_id:'hidden-wire',orchestrator_phase:'running',
      nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}])),
      edges:[{from:'agent-hub',to:command.id}],event:{type:'TOOL_CALL_START',tool:'bash',call_id:'hidden-call',input:'{"command":"fixture"}'}};
    viewMode='live';steps=[snapshot];showStep(0);
    projectHiddenBlocks=['commands','documents','tools','memory','files','capabilities'];applyBlockVisibility();arrangeBySize();
    await ${nextFrames};
    const groups=[...document.querySelectorAll('.group:not([hidden])')].map(el=>({id:el.dataset.group,x:el.offsetLeft,y:el.offsetTop,w:el.offsetWidth,h:el.offsetHeight}));
    const agent=groups.find(g=>g.id==='agent');
    return{groups,edges:svg.querySelectorAll('g[data-edge]').length,agent:[agent.x+agent.w/2,agent.y+agent.h/2],
      center:[(Math.min(...groups.map(g=>g.x))+Math.max(...groups.map(g=>g.x+g.w)))/2,(Math.min(...groups.map(g=>g.y))+Math.max(...groups.map(g=>g.y+g.h)))/2]};
  })()`);
  assert.deepEqual(result.groups.map(g=>g.id).sort(),['agent','request','result']);
  assert.equal(result.edges,0);
  for(let axis=0;axis<2;axis++)assert.ok(Math.abs(result.agent[axis]-result.center[axis])<=1);
  for(let i=0;i<result.groups.length;i++)for(let j=i+1;j<result.groups.length;j++){
    const a=result.groups[i],b=result.groups[j];
    assert.ok(a.x+a.w+95<=b.x||b.x+b.w+95<=a.x||a.y+a.h+95<=b.y||b.y+b.h+95<=a.y);
  }
});

test('Adaptive block packing sizes blocks from content into a single rectangle and preserves geometry per session', async () => {
  sessionFixtures=new Map([[config.project_root,[
    {session_id:'session-a',session_name:'Session A',application:'Codex',prompt:'Fit a bracket.'},
    {session_id:'session-b',session_name:'Session B',application:'Codex',prompt:'Detailed bracket specification with mounting constraints. '.repeat(40)}
  ]],[alternateRoot,[]]]);
  try {
    await preparePage();
    const result=await evaluate(`(async()=>{
      await refreshSessions();await selectSession('session-a');
      projectHiddenBlocks=[];applyBlockVisibility();
      const geometry=()=>[...document.querySelectorAll('.group:not([hidden])')].map(el=>({id:el.dataset.group,x:el.offsetLeft,y:el.offsetTop,w:el.offsetWidth,h:el.offsetHeight}));
      const natural=geometry();
      arrangeBySize();await ${nextFrames};
      const packed=geometry(),agent=packed.find(g=>g.id==='agent'),gap=96;
      const left=Math.min(...packed.map(g=>g.x)),right=Math.max(...packed.map(g=>g.x+g.w)),top=Math.min(...packed.map(g=>g.y)),bottom=Math.max(...packed.map(g=>g.y+g.h));
      const separated=packed.every((a,i)=>packed.slice(i+1).every(b=>a.x+a.w+gap<=b.x||b.x+b.w+gap<=a.x||a.y+a.h+gap<=b.y||b.y+b.h+gap<=a.y));
      await selectSession('session-b');await ${nextFrames};
      const inherited=JSON.stringify(geometry())===JSON.stringify(packed);
      arrangeBySize();await ${nextFrames};
      const contentChangesSizes=geometry().some((g,i)=>g.w!==packed[i].w||g.h!==packed[i].h);
      await selectSession('session-a');await ${nextFrames};
      const restoredGroups=geometry(),restored=JSON.stringify(restoredGroups)===JSON.stringify(packed);
      document.querySelector('#reset-layout').click();await ${nextFrames};
      const resetChangesSizes=geometry().some((g,i)=>g.w!==packed[i].w||g.h!==packed[i].h);
      return{
        resized:packed.some((g,i)=>g.w!==natural[i].w||g.h!==natural[i].h),
        separated,agentMinH:agent.h>=484,
        agentCenter:[agent.x+agent.w/2,agent.y+agent.h/2],
        expectedCenter:[(left+right)/2,(top+bottom)/2],
        tileArea:packed.reduce((sum,g)=>sum+(g.w+gap)*(g.h+gap),0),
        boundingArea:(right-left+gap)*(bottom-top+gap),
        inherited,contentChangesSizes,restored,resetChangesSizes,restoreDifferences:packed.flatMap((g,i)=>JSON.stringify(g)===JSON.stringify(restoredGroups[i])?[]:[{before:g,after:restoredGroups[i]}])
      };
    })()`);
    assert.equal(result.resized,true);
    assert.equal(result.separated,true);
    assert.equal(result.agentMinH,true);
    assert.equal(result.tileArea,result.boundingArea);
    for(let axis=0;axis<2;axis++)assert.ok(Math.abs(result.agentCenter[axis]-result.expectedCenter[axis])<=1);
    assert.equal(result.inherited,false);
    assert.equal(result.contentChangesSizes,true);
    assert.equal(result.restored,true,JSON.stringify(result.restoreDifferences));
    assert.equal(result.resetChangesSizes,true);
    const beforeReload=await evaluate("(()=>{arrangeBySize();return [...document.querySelectorAll('.group:not([hidden])')].map(el=>[el.dataset.group,el.offsetLeft,el.offsetTop,el.offsetWidth,el.offsetHeight])})()");
    await preparePage(base+'?session=session-a');
    const afterReload=await evaluate("[...document.querySelectorAll('.group:not([hidden])')].map(el=>[el.dataset.group,el.offsetLeft,el.offsetTop,el.offsetWidth,el.offsetHeight])");
    assert.deepEqual(afterReload,beforeReload);
  } finally {
    sessionFixtures=null;
    await evaluate("(()=>{for(const key of Object.keys(localStorage))if(key.includes('cad-map-packed:')&&(key.endsWith(':session-a')||key.endsWith(':session-b')))localStorage.removeItem(key)})()");
  }
});

test('Settings disclosures preserve manual expansion across refresh and search without changing canvas visibility', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    graphSettings.open({path:config.project_root,name:'Current project',current:true});
    const deadline=performance.now()+5000;
    while(!graphSettings.data&&performance.now()<deadline)await new Promise(requestAnimationFrame);
    graphSettings.data.rows.unshift(...[['documents','Documents & references'],['capabilities','MCP & skills']].map(([group,name])=>({id:'block:'+group,group,name,kind:'block'})));
    graphSettings.render();
    const visibility=document.querySelector('#group-visibility-documents').checked;
    const initial=document.querySelector('#graph-settings-group-documents').hidden;
    document.querySelector('#project-role-disclosure').click();
    document.querySelector('#group-disclosure-documents').click();
    document.querySelector('#group-disclosure-documents').focus();
    graphSettings.render();
    const refreshed={role:document.querySelector('#project-role-disclosure').getAttribute('aria-expanded'),documents:document.querySelector('#graph-settings-group-documents').hidden,visibility:document.querySelector('#group-visibility-documents').checked,focus:document.activeElement.id};
    graphSettings.filter.value='mcp';graphSettings.filter.onchange();
    const filtered={documents:document.querySelector('[data-group=documents].graph-settings-group').hidden,mcp:document.querySelector('#graph-settings-group-capabilities').hidden};
    graphSettings.filter.value='';graphSettings.filter.onchange();
    const restored={documents:document.querySelector('#graph-settings-group-documents').hidden,mcp:document.querySelector('#graph-settings-group-capabilities').hidden};
    graphSettings.dialog.close();return{initial,visibility,refreshed,filtered,restored};
  })()`);
  assert.equal(result.initial,true);
  assert.deepEqual(result.refreshed,{role:'true',documents:false,visibility:result.visibility,focus:'group-disclosure-documents'});
  assert.deepEqual(result.filtered,{documents:true,mcp:false});
  assert.deepEqual(result.restored,{documents:false,mcp:true});
});

test('A vault edit can be cancelled when a background inventory refresh arrives between pointer down and pointer up', async () => {
  await preparePage();
  const point=await evaluate(`(async()=>{
    graphSettings.open({path:config.project_root,name:'Current project',current:true});
    const deadline=performance.now()+5000;
    while(!graphSettings.data&&performance.now()<deadline)await new Promise(requestAnimationFrame);
    graphSettings.data.rows.unshift({id:'block:documents',group:'documents',name:'Documents & references',kind:'block'});
    graphSettings.vaultDraft={project:config.project_root,revision:'fixture',value:'/tmp/unsaved-vault'};
    graphSettings.disclosureState().groups.documents=true;graphSettings.render();
    const button=document.querySelector('.graph-settings-vault-actions button[type=button]'),box=button.getBoundingClientRect();
    return{x:box.x+box.width/2,y:box.y+box.height/2};
  })()`);
  await command('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
  await evaluate('graphSettings.render()');
  await command('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});
  const closed=await evaluate('!graphSettings.vaultDraft&&!document.querySelector(".graph-settings-vault")');
  assert.equal(closed,true);
});

test('Resource eyes hide only their card and wires, survive reload, and protect required blocks', async () => {
  visibilityPreferences=new Map([config.project_root,alternateRoot].map(root=>[root,{revision:'1',hidden_blocks:[],hidden_resources:[],hidden_commands:[]}]));
  try{
    await preparePage();
    const result=await evaluate(`(async()=>{
      const frame=()=>new Promise(requestAnimationFrame),wait=async test=>{const end=performance.now()+5000;while(!test()&&performance.now()<end)await frame();if(!test())throw Error('Visibility update timed out')};
      viewMode='live';
      renderState({run_id:'eye-evidence',nodes:{mcp_demo:{...config.nodes.find(n=>n.id==='mcp_demo'),status:'observed'}},
        edges:[{from:'agent-hub',to:'mcp_demo'},{from:'agent-hub',to:'tool_0'}],orchestrator_phase:'idle'});
      await frame();await frame();
      graphSettings.open({path:config.project_root,name:'Current project',current:true});await wait(()=>graphSettings.data);
      const before=[...svg.querySelectorAll('g[data-edge]')].some(el=>wireData.get(el.dataset.edge)?.toId==='mcp_demo');
      const eye=()=>graphSettings.list.querySelector('[data-feature-id="mcp_current"] [data-action=visibility]');
      const initiallyEnabled=!eye().disabled;
      eye().click();await wait(()=>!graphSettings.savingResource);await frame();await frame();
      const hidden={card:document.querySelector('#mcp_demo').getBoundingClientRect().width,endpoint:baseEndpoint('mcp_demo'),
        edges:documentGraph().edges.map(edge=>edge.to),recorded:state.edges.map(edge=>edge.to),drawn:[...svg.querySelectorAll('g[data-edge]')].map(el=>wireData.get(el.dataset.edge)?.toId),
        pressed:eye().getAttribute('aria-pressed'),other:document.querySelector('#tool_0').getBoundingClientRect().width>0};
      const required=['request_prompt','agent-hub','run_result'].map(id=>graphSettings.list.querySelector('[data-feature-id="'+id+'"] [data-action=visibility]')?.disabled);
      graphSettings.dialog.close();return{before,initiallyEnabled,hidden,required};
    })()`);
    assert.equal(result.initiallyEnabled,true);
    assert.equal(result.before,true);assert.equal(result.hidden.drawn.includes('mcp_demo'),false);
    assert.equal(result.hidden.card,0);assert.equal(result.hidden.endpoint,null);
    assert.equal(result.hidden.edges.includes('mcp_demo'),false);assert.equal(result.hidden.edges.includes('tool_0'),true);
    assert.deepEqual(result.hidden.recorded,['mcp_demo','tool_0']);assert.equal(result.hidden.other,true);
    assert.equal(result.hidden.pressed,'false');assert.deepEqual(result.required,[true,true,true]);
    await preparePage();
    assert.equal(await evaluate("document.querySelector('#mcp_demo').getBoundingClientRect().width"),0);
    const restored=await evaluate(`(async()=>{
      graphSettings.open({path:config.project_root,name:'Current project',current:true});
      while(!graphSettings.data)await new Promise(requestAnimationFrame);
      const eye=()=>graphSettings.list.querySelector('[data-feature-id="mcp_current"] [data-action=visibility]');
      eye().click();while(graphSettings.savingResource)await new Promise(requestAnimationFrame);
      const result={visible:document.querySelector('#mcp_demo').getBoundingClientRect().width>0,pressed:eye().getAttribute('aria-pressed')};
      graphSettings.dialog.close();return result;
    })()`);
    assert.deepEqual(restored,{visible:true,pressed:'true'});
  }finally{visibilityPreferences=null}
});

test('Hidden documents cannot acquire folder or portal wires in either view and retain their recorded calls', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(requestAnimationFrame),settle=async()=>{await frame();await frame()};
    viewMode='saved';viewRunId='hidden-doc-evidence';
    const docs=config.nodes.filter(node=>node.kind==='document'),hidden=docs[0],visible=docs[1];
    const event={type:'TOOL_CALL_START',tool:'read',call_id:'hidden-read',input:{path:hidden.path}};
    steps=[{event,run_id:viewRunId,nodes:Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:node.id===hidden.id?'hit':'idle'}])),
      edges:[{from:'agent-hub',to:hidden.id},{from:'agent-hub',to:visible.id}],orchestrator_phase:'running'}];
    currentStepIndex=0;showStep(0);await settle();
    if(routeInvocationCache.report.total!==1)throw Error('The recorded read call is missing');
    const evidence=JSON.stringify(routeInvocationCache.report),checks=[];
    captureVisibilityDefaults({...config,hidden_resources:[ObserverGraphSettings.resourceKey(hidden,config)]});
    renderCatalog(false);showStep(0);
    for(const mode of ['list','bubble']){
      applyDocumentsView(mode);await settle();
      for(const folder of document.querySelectorAll('#documents-container details'))folder.open=false;
      renderEdges();await settle();
      const graph=documentGraph();
      checks.push({mode,endpoint:baseEndpoint(hidden.id),linked:graph.linked.has(hidden.id),visible:graph.linked.has(visible.id),
        ghostBundle:graph.edges.some(edge=>(edge.members||[]).includes(hidden.id)),evidence:JSON.stringify(routeInvocationCache.report)===evidence});
    }
    const moved={...hidden,id:hidden.id+'_rescanned'};
    config={...config,nodes:config.nodes.map(node=>node.id===hidden.id?moved:node)};
    renderCatalog(false);renderState({run_id:'new-session',nodes:{},edges:[],orchestrator_phase:'idle'});await settle();
    const rescanned={hidden:isResourceHidden(moved),endpoint:baseEndpoint(moved.id),other:isResourceHidden(visible)};
    captureVisibilityDefaults({...config,hidden_resources:[]});renderCatalog(false);renderState(state);await settle();
    return{checks,rescanned,restored:Boolean(baseEndpoint(moved.id))};
  })()`);
  for(const check of result.checks){
    assert.equal(check.endpoint,null,check.mode);assert.equal(check.linked,false,check.mode);assert.equal(check.visible,true,check.mode);
    assert.equal(check.ghostBundle,false,check.mode);assert.equal(check.evidence,true,check.mode);
  }
  assert.deepEqual(result.rescanned,{hidden:true,endpoint:null,other:false});assert.equal(result.restored,true);
});

test('Command eyes preserve an unhide control and share visibility with command block settings', async () => {
  visibilityPreferences=new Map([config.project_root,alternateRoot].map(root=>[root,{revision:'1',hidden_blocks:[],hidden_resources:[],hidden_commands:[]}]));
  try{
    await preparePage();
    const result=await evaluate(`(async()=>{
      const frame=()=>new Promise(requestAnimationFrame),wait=async test=>{const end=performance.now()+5000;while(!test()&&performance.now()<end)await frame();if(!test())throw Error('Command visibility timed out')};
      viewMode='saved';viewRunId='command-eye';
      steps=[{run_id:viewRunId,event:{type:'TOOL_CALL_START',tool:'read',call_id:'command-eye-read',input:{path:config.nodes.find(node=>node.kind==='document').path}},nodes:{},edges:[],orchestrator_phase:'running'}];
      showStep(0);await frame();
      graphSettings.open({path:config.project_root,name:'Current project',current:true});await wait(()=>graphSettings.data);
      const item=graphSettings.inventoryRows().find(item=>item.kind==='command'&&item.node_id===commandTemplateFor('read').id);
      if(!item)throw Error('Recorded read command is absent from inventory');
      const eye=()=>graphSettings.list.querySelector('[data-feature-id="'+item.id+'"] [data-action=visibility]');
      eye().click();await wait(()=>!graphSettings.savingResource);await frame();
      const hidden={present:Boolean(eye()),pressed:eye()?.getAttribute('aria-pressed'),endpoint:baseEndpoint(item.node_id),
        blockVisible:blockSettings.commandVisible({id:item.node_id}),calls:routeInvocationCache.report.total};
      renderState({run_id:'another-snapshot',nodes:{},edges:[],orchestrator_phase:'idle'});
      const staysHidden=baseEndpoint(item.node_id)===null;
      graphSettings.render();eye().click();await wait(()=>!graphSettings.savingResource);await frame();
      const restored={card:Boolean(document.getElementById(item.node_id)),blockVisible:blockSettings.commandVisible({id:item.node_id})};
      graphSettings.dialog.close();return{hidden,staysHidden,restored};
    })()`);
    assert.deepEqual(result.hidden,{present:true,pressed:'false',endpoint:null,blockVisible:false,calls:1});
    assert.equal(result.staysHidden,true);assert.deepEqual(result.restored,{card:true,blockVisible:true});
  }finally{visibilityPreferences=null}
});

test('Session deletion requires confirmation, removes the selected chat, and rejects stale sidebar updates', async () => {
  sessionFixtures=new Map([[config.project_root,[{session_id:'idle-session',session_name:'Delete this chat',application:'Codex'},{session_id:'keep-session',session_name:'Keep this chat',application:'Codex'}]],[alternateRoot,[]]]);
  try{
    await preparePage();
    await evaluate("(async()=>{await refreshSessionProjects();await refreshSessions();await selectSession('idle-session')})()");
    const start=requests.length;
    const cancelled=await evaluate(`(()=>{
      document.querySelector('.session-chat-more[data-session="idle-session"]').click();
      document.querySelector('.session-delete-action').click();
      const focused=document.activeElement.classList.contains('session-delete-cancel');
      document.querySelector('.session-delete-cancel').click();
      return{focused,present:liveSessions.has('idle-session'),selected:selectedSessionId};
    })()`);
    assert.deepEqual(cancelled,{focused:true,present:true,selected:'idle-session'});
    assert.equal(requests.slice(start).some(request=>request.url==='/api/session-delete'),false);
    const removed=await evaluate(`(async()=>{
      document.querySelector('.session-chat-more[data-session="idle-session"]').click();
      document.querySelector('.session-delete-action').click();document.querySelector('.session-delete-confirm').click();
      const end=performance.now()+5000;while(sessionDeleteBusy&&performance.now()<end)await new Promise(requestAnimationFrame);
      mergeSessionList({sessions:[{session_id:'idle-session',application:'Codex'}]},false);
      onWsMessage({type:'STATE_UPDATE',session_id:'idle-session',state:{run_id:'late-deleted',prompt:'Deleted request',context:{session_id:'idle-session'},nodes:{},edges:[],orchestrator_phase:'running'}});
      return{present:liveSessions.has('idle-session'),selected:selectedSessionId,rendered:snapshotSession(state),
        oldButton:Boolean(document.querySelector('.session-chat[data-session="idle-session"]')),modal:sessionDeleteDialog.open};
    })()`);
    assert.deepEqual(removed,{present:false,selected:'keep-session',rendered:'keep-session',oldButton:false,modal:false});
  }finally{sessionFixtures=null}
});

test('Session selection shares in-flight requests and never paints a blank or a late session over the selected chat', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    clearSessionDetailLoads();liveSessions.clear();
    const detail=(id,prompt)=>({config,history:[],state:{run_id:'run-'+id,prompt,context:{session_id:id},nodes:{},edges:[],orchestrator_phase:'idle'}});
    const old={session_id:'before',loaded:true,version:0,...detail('before','Previous graph')};
    liveSessions.set('before',old);selectedSessionId='before';displaySession(old);
    for(const id of ['slow','fast'])liveSessions.set(id,{session_id:id,version:0,history:[],loaded:false});
    const native=documentRequest,resolvers={},calls={slow:0,fast:0};
    documentRequest=(url,...args)=>{const id=url.split('/').at(-1);if(id in calls){calls[id]++;return new Promise(resolve=>resolvers[id]=resolve)}return native(url,...args)};
    try{
      const slow=selectSession('slow'),same=selectSession('slow'),fast=selectSession('fast');
      const pending={prompt:state.prompt,busy:viewport.getAttribute('aria-busy'),blocked:viewport.inert};
      resolvers.fast(detail('fast','Fast graph'));await fast;
      resolvers.slow(detail('slow','Slow graph'));await Promise.all([slow,same]);
      const after={selected:selectedSessionId,prompt:state.prompt,busy:viewport.getAttribute('aria-busy')};
      await selectSession('slow');
      return{pending,after,calls,cached:{selected:selectedSessionId,prompt:state.prompt}};
    }finally{documentRequest=native}
  })()`);
  assert.deepEqual(result.pending,{prompt:'Previous graph',busy:'true',blocked:true});
  assert.deepEqual(result.after,{selected:'fast',prompt:'Fast graph',busy:'false'});
  assert.deepEqual(result.calls,{slow:1,fast:1});assert.deepEqual(result.cached,{selected:'slow',prompt:'Slow graph'});
});

test('Sessions switch across projects without document navigation, sidebar collapse or duplicate detail loads', async () => {
  sessionFixtures=new Map([[config.project_root,[{session_id:'same-id',session_name:'Home chat',application:'Codex',prompt:'Home selected request'}]],[alternateRoot,[{session_id:'same-id',session_name:'Foreign chat',application:'Codex',prompt:'Foreign selected request'}]]]);
  try{
    await preparePage();
    await evaluate(`(async()=>{await refreshSessionProjects();window.navigationProbe={document,origin:performance.timeOrigin};for(const folder of document.querySelectorAll('.session-project'))folder.open=true})()`);
    const start=requests.length;
    for(const [root,prompt] of [[alternateRoot,'Foreign selected request'],[config.project_root,'Home selected request'],[alternateRoot,'Foreign selected request']]){
      await evaluate(`(()=>{const row=[...document.querySelectorAll('.session-chat')].find(el=>el.dataset.project===${JSON.stringify(root)});row.click()})()`);
      for(let attempt=0;attempt<100;attempt++){if(await evaluate(`config.project_root===${JSON.stringify(root)}&&state?.prompt===${JSON.stringify(prompt)}&&!viewport.inert`))break;await pause(30)}
      const result=await evaluate(`({root:config.project_root,session:snapshotSession(state),prompt:state.prompt,setup:document.querySelectorAll('.observer-connections-dialog[open]').length,sameDocument:window.navigationProbe?.document===document,sameOrigin:window.navigationProbe?.origin===performance.timeOrigin,folders:[...document.querySelectorAll('.session-project')].every(el=>el.open)})`);
      assert.deepEqual(result,{root,session:'same-id',prompt,setup:0,sameDocument:true,sameOrigin:true,folders:true});
    }
    assert.equal(requests.slice(start).filter(row=>row.url.startsWith('/api/session-open')).length,1,'Returning to warm projects must not reopen their server');
    assert.equal(requests.slice(start).filter(row=>row.url==='/'||row.url.startsWith('/other-project/')).length,0,'Switching must never fetch a new dashboard document');
    assert.equal(requests.slice(start).filter(row=>row.url.startsWith('/api/sessions/same-id')).length,0,'Warm session details stay in their project cache');
  }finally{sessionFixtures=null}
});

test('One folder lists codex and OMP badges and opens each source independently', async () => {
  const chats=[
    {session_id:'codex-chat',session_name:'Same folder Codex',application:'Codex',prompt:'Codex isolated request'},
    {session_id:'omp:omp-chat',session_name:'Same folder OMP',application:'OMP',prompt:'OMP isolated request'},
  ];
  sessionFixtures=new Map([[config.project_root,chats]]);
  try{
    await preparePage();
    await evaluate('(async()=>{await refreshSessions();await refreshSessionProjects()})()');
    const badges=await evaluate(`([...document.querySelectorAll('.session-chat')].filter(row=>row.dataset.project===${JSON.stringify(config.project_root)}).map(row=>({id:row.dataset.session,agent:row.querySelector('.session-chat-agent')?.textContent,small:parseFloat(getComputedStyle(row.querySelector('.session-chat-agent')).fontSize)<=11})))`);
    assert.deepEqual(badges.filter(row=>chats.some(chat=>chat.session_id===row.id)),[
      {id:'codex-chat',agent:'codex',small:true},{id:'omp:omp-chat',agent:'OMP',small:true},
    ]);
    for(const chat of [chats[0],chats[1],chats[0]]){
      await evaluate(`([...document.querySelectorAll('.session-chat')].find(row=>row.dataset.session===${JSON.stringify(chat.session_id)}).click())`);
      for(let attempt=0;attempt<100;attempt++){
        if(await evaluate(`snapshotSession(state)===${JSON.stringify(chat.session_id)}&&state?.prompt===${JSON.stringify(chat.prompt)}&&!viewport.inert`))break;
        await pause(30);
      }
      assert.deepEqual(await evaluate('({id:snapshotSession(state),prompt:state.prompt})'),{id:chat.session_id,prompt:chat.prompt});
    }
  }finally{sessionFixtures=null}
});

test('Session selection preserves disclosure nodes and scroll while activity bursts retain all events in one frame', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    clearSessionDetailLoads();liveSessions.clear();
    for(const id of ['one','two'])liveSessions.set(id,{session_id:id,session_name:id,loaded:true,version:0,config,history:[],state:{run_id:id,prompt:id,nodes:{},edges:[],context:{session_id:id},orchestrator_phase:'idle'}});
    selectedSessionId='one';displaySession(liveSessions.get('one'));renderSessionTabs();
    const folder=document.querySelector('.session-project'),row=folder.querySelector('.session-chat');folder.open=false;
    await selectSession('two');
    const preserved={folder:folder===document.querySelector('.session-project'),row:row===document.querySelector('.session-chat'),closed:!folder.open};
    const deadline=performance.now()+2000;while(world.classList.contains('camera-moving')&&performance.now()<deadline)await new Promise(requestAnimationFrame);
    let paints=0;const native=renderState;renderState=snapshot=>{paints++;return native(snapshot)};
    try{
      for(let index=0;index<25;index++){const snapshot={run_id:'two',prompt:'two',nodes:{},edges:[],context:{session_id:'two'},orchestrator_phase:'running'};onWsMessage({type:'STATE_UPDATE',session_id:'two',state:snapshot,history_step:{...snapshot,step_index:index,event:{type:'THINKING',phase:'event '+index}}})}
      const immediate=paints;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      return {preserved,immediate,paints,events:liveSteps.length,shown:currentStepIndex};
    }finally{renderState=native}
  })()`);
  assert.deepEqual(result.preserved,{folder:true,row:true,closed:true});
  assert.equal(result.immediate,0);assert.equal(result.paints,1);
  assert.equal(result.events,25);assert.equal(result.shown,24);
});

test('Layout choices stay next to their rail control and terminal activity animates only the glyph', async () => {
  localServices=[{id:'browser-fixture',name:'Scoped service',status:'running',connected:true,owner:'external',log_available:true}];
  try{
    await preparePage();
    await command('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
    const result=await evaluate(`(async()=>{
      await refreshProjectHosts();projectHosts.set(config.project_root,{services:${JSON.stringify(localServices)}});renderSessionTabs();
      document.querySelector('#auto-layout').click();
      const menu=document.querySelector('#layout-options').getBoundingClientRect(),button=document.querySelector('#auto-layout').getBoundingClientRect();
      const adjacent=menu.left>=button.right&&menu.left-button.right<24&&Math.abs(menu.top-button.top)<24;
      const contained=menu.left>=0&&menu.top>=0&&menu.right<=innerWidth&&menu.bottom<=innerHeight;
      document.querySelector('#layout-options').hidePopover();
      const folders=[...document.querySelectorAll('.session-project')],allHaveTerminal=folders.every(folder=>folder.querySelector('.session-project-host'));
      const host=folders.find(folder=>folder.dataset.project===config.project_root).querySelector('.session-project-host');
      const pulse={glyph:getComputedStyle(host.querySelector('path')).animationName,button:getComputedStyle(host).animationName,decoration:getComputedStyle(host,'::after').content};
      host.click();await new Promise(requestAnimationFrame);
      const blocked=[...document.querySelectorAll('#activity-rail button')].every(button=>button.disabled);
      const visible=[...document.querySelectorAll('#activity-rail .canvas-tools,#activity-rail .canvas-controls')].every(el=>getComputedStyle(el).display!=='none'&&getComputedStyle(el).visibility!=='hidden');
      document.querySelector('#terminal-close').click();
      return{adjacent,contained,allHaveTerminal,pulse,blocked,visible,returned:!document.body.classList.contains('terminal-active'),enabled:[...document.querySelectorAll('#activity-rail button')].every(button=>!button.disabled)};
    })()`);
    assert.equal(result.adjacent,true);assert.equal(result.contained,true);assert.equal(result.allHaveTerminal,true);
    assert.notEqual(result.pulse.glyph,'none');assert.equal(result.pulse.button,'none');assert.equal(result.pulse.decoration,'none');
    assert.equal(result.blocked,true);assert.equal(result.visible,true);assert.equal(result.returned,true);assert.equal(result.enabled,true);
  }finally{localServices=[]}
});

test('Foreign block visibility remains consistent with saved preferences after hiding and restoring', async () => {
  visibilityPreferences=new Map([config.project_root,alternateRoot].map(root=>[root,{revision:'1',hidden_blocks:[],hidden_resources:[],hidden_commands:[]}]));
  try{
    await preparePage();
    const result=await evaluate(`(async()=>{
      const original=graphSettings.request;
      graphSettings.request=async(url,body)=>{
        const data=await original(url,body);
        if(url==='/api/graph-settings'){data.revision=data.config.revision;data.hidden_blocks=data.config.hidden_blocks}
        if(url==='/api/graph-settings')data.rows=[
          {id:'block-documents',kind:'block',group:'documents',name:'Documents'},
          ...data.config.nodes.filter(node=>node.kind==='document').map(node=>({id:node.id,node_id:node.id,kind:'document',group:'documents',name:node.path,in_graph:true}))
        ];
        return data;
      };
      try{
        graphSettings.open({path:'${alternateRoot}',name:'Other project',current:false});
        const deadline=performance.now()+5000;
        while(!graphSettings.data&&performance.now()<deadline)await new Promise(requestAnimationFrame);
        const samples=[];
        for(const visible of [false,true]){
          await graphSettings.setBlockVisibility('documents',visible);
          const saved=await graphSettings.request('/api/block-settings');
          samples.push({visible,checked:document.querySelector('#group-visibility-documents').checked,
            hidden:saved.hidden_blocks.includes('documents'),count:document.querySelector('[data-group="documents"] .graph-settings-group-count').textContent});
        }
        return{samples,currentHidden:(graphSettings.getConfig().hidden_blocks||[]).includes('documents')};
      }finally{graphSettings.request=original;graphSettings.dialog.close()}
    })()`);
    assert.equal(result.currentHidden,false);
    assert.equal(result.samples[0].checked,false);assert.equal(result.samples[0].hidden,true);
    assert.equal(result.samples[0].count.startsWith('0 / '),true);
    assert.equal(result.samples[1].checked,true);assert.equal(result.samples[1].hidden,false);
    assert.equal(result.samples[1].count.startsWith('0 / '),false);
  }finally{visibilityPreferences=null}
});

test('Save shortcut cannot discard invalid memory JSON or persist its previous valid value', async () => {
  await preparePage();
  await evaluate(`(async()=>{
    window.__invalidMemoryRequest=blockSettings.api.request;window.__invalidMemoryWrites=0;
    const model={revision:'fixture',documents:[],tools:[],hidden_commands:[],block_layouts:{},capabilities:{mcp:[],skills:[]},
      memory_systems:[{id:'invalid-fixture',name:'Manual memory',provider:'custom',endpoint:'',bank_ids:[],categories:[],stores:[],match:{tool_names:[]}}]};
    blockSettings.api.request=async(url,body)=>{if(body)window.__invalidMemoryWrites++;return structuredClone(model)};
    await blockSettings.open('memory',document.querySelector('.g-memory .group-settings-button'));
    document.querySelector('.memory-settings-entry summary').click();
    const field=document.querySelector('textarea.memory-settings-json');field.focus();field.select();
  })()`);
  try{
    await command('Input.insertText',{text:'['});
    await command('Input.dispatchKeyEvent',{type:'rawKeyDown',key:'s',code:'KeyS',windowsVirtualKeyCode:83,modifiers:2});
    await command('Input.dispatchKeyEvent',{type:'keyUp',key:'s',code:'KeyS',windowsVirtualKeyCode:83,modifiers:2});
    await evaluate(nextFrames);
    const result=await evaluate("({writes:window.__invalidMemoryWrites,draft:document.querySelector('textarea.memory-settings-json').value,dirty:blockSettings.dirty,blocked:blockSettings.saveButton.disabled})");
    assert.deepEqual(result,{writes:0,draft:'[',dirty:true,blocked:true});
  }finally{await evaluate("blockSettings.api.request=window.__invalidMemoryRequest;blockSettings.dirty=false;blockSettings.close();delete window.__invalidMemoryRequest;delete window.__invalidMemoryWrites")}
});

test('Memory resource settings retain edits made after submission when the saved response arrives', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const holder=document.createElement('section');holder.dataset.resourceTab='settings';document.body.append(holder);
    let current={revision:'one',memory_systems:[{id:'draft-fixture',name:'Original memory',provider:'custom',endpoint:'',bank_ids:['original'],categories:[],stores:[],match:{tool_names:[]}}]};
    let resolveSave,submitted;
    const view=new window.ObserverMemorySystems({
      getConfig:()=>({nodes:[],memory_systems:current.memory_systems}),getState:()=>({nodes:{}}),
      request:async(url,body)=>{
        if(body){submitted=structuredClone(body);return new Promise(resolve=>resolveSave=resolve)}
        return url==='/api/memory-systems'?{systems:[]}:structuredClone(current);
      },onConfigSaved:()=>{}
    });
    const node={id:'memory-draft-fixture',memory_system_id:'draft-fixture'};
    try{
      await view.renderSettings(holder,node);
      const name=holder.querySelector('#mem-set-name');name.value='Submitted name';name.dispatchEvent(new Event('input',{bubbles:true}));
      const pending=holder.querySelector('#mem-set-save').onclick();
      const banks=holder.querySelector('#mem-set-banks');banks.value='newer-unsaved-bank';banks.dispatchEvent(new Event('input',{bubbles:true}));
      current={revision:'two',memory_systems:submitted.memory_systems};
      resolveSave(structuredClone(current));await pending;
      await view.renderSettings(holder,node);
      return{draftBank:holder.querySelector('#mem-set-banks').value,name:holder.querySelector('#mem-set-name').value,
        savedBank:current.memory_systems[0].bank_ids[0],revision:view.settingsDrafts.get('draft-fixture')?.current.revision};
    }finally{view.settingsGuide?.destroy();holder.remove()}
  })()`);
  assert.deepEqual(result,{draftBank:'newer-unsaved-bank',name:'Submitted name',savedBank:'original',revision:'two'});
});

test('Russian inventory search matches visible translated labels while retaining English search', async () => {
  await preparePage();
  try{
    const samples=await evaluate(`(async()=>{
      const original=graphSettings.request;
      graphSettings.request=async(url,body)=>{
        const data=await original(url,body);
        if(url==='/api/graph-settings')data.rows=[
          {id:'block-request',kind:'block',group:'request',name:'Project prompt & refinements'},
          {id:'prompt-search',kind:'prompt',group:'request',node_id:'request_prompt',name:'Project prompt',in_graph:true}
        ];
        return data;
      };
      window.ObserverI18n.setLanguage('ru');
      try{
        graphSettings.open({path:'${alternateRoot}',name:'Other project',current:false});
        const deadline=performance.now()+5000;
        while(!graphSettings.data&&performance.now()<deadline)await new Promise(requestAnimationFrame);
        const samples=[];
        for(const query of ['Промпт','Project prompt']){
          graphSettings.search.value=query;graphSettings.search.dispatchEvent(new Event('input',{bubbles:true}));
          await ${nextFrames};
          const row=graphSettings.list.querySelector('[data-feature-id="prompt-search"]');
          samples.push({query,present:Boolean(row),label:row?.querySelector('strong').textContent});
        }
        return samples;
      }finally{graphSettings.request=original;graphSettings.dialog.close()}
    })()`);
    for(const sample of samples){assert.equal(sample.present,true,sample.query);assert.match(sample.label,/Промпт/)}
  }finally{await evaluate("window.ObserverI18n.setLanguage('en')")}
});

test('Compact chrome keeps a fixed application name and usable footer controls with truthful status tooltips', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const name=document.querySelector('#app-name').textContent;
    const originalTheme=document.documentElement.dataset.theme,originalLanguage=ObserverI18n.language;
    const bottomHidden=document.body.classList.contains('bottom-hidden');
    document.querySelector('#toggle-bottom').click();const toggled=document.body.classList.contains('bottom-hidden')!==bottomHidden;
    document.querySelector('#toggle-bottom').click();
    document.querySelector('#theme-toggle').click();const themeChanged=document.documentElement.dataset.theme!==originalTheme;
    document.querySelector('#theme-toggle').click();
    const phases=[];
    for(const phase of ['running','waiting','error','complete']){
      onWsMessage({type:'STATE_UPDATE',config:{...config,project_title:'A different folder'},state:{run_id:'chrome-status',nodes:{},edges:[],orchestrator_phase:phase}});
      await frame();phases.push(document.querySelector('#source').dataset.state);
    }
    document.querySelector('#language-toggle').click();await frame();
    const localized=document.querySelector('#source').getAttribute('aria-label');
    document.querySelector('#language-toggle').click();await frame();
    document.querySelector('#history-tab').click();const historyOpened=document.body.classList.contains('history-active');
    document.querySelector('#history-tab').click();
    const controls=['toggle-bottom','history-tab','theme-toggle','language-toggle'].map(id=>{const el=document.getElementById(id);return{id,inFooter:!!el.closest('.app-statusbar'),inRail:!!el.closest('.activity-rail-bottom'),pressed:el.getAttribute('aria-pressed')}});
    const status=document.querySelector('#source'),box=status.getBoundingClientRect();
    return{name,afterName:document.querySelector('#app-name').textContent,title:document.title,logo:!!document.querySelector('.brand-mark'),
      phases,localized,languageRestored:ObserverI18n.language===originalLanguage,themeRestored:document.documentElement.dataset.theme===originalTheme,
      toggled,bottomRestored:document.body.classList.contains('bottom-hidden')===bottomHidden,historyOpened,controls,
      inspectorInHeader:!!document.querySelector('header #toggle-inspector'),position:{x:box.x+box.width/2,y:box.y+box.height/2},statusRole:status.getAttribute('role')};
  })()`);
  assert.equal(result.name,'Agent Observed');assert.equal(result.afterName,result.name);assert.equal(result.title,result.name);assert.equal(result.logo,false);
  assert.deepEqual(result.phases,['running','waiting','error','complete']);
  assert.match(result.localized,/Статус сессии/);assert.ok(result.languageRestored&&result.themeRestored&&result.toggled&&result.bottomRestored&&result.historyOpened);
  assert.ok(result.controls.filter(control=>control.id!=='toggle-bottom').every(control=>control.inFooter));assert.equal(result.controls.find(control=>control.id==='toggle-bottom').inRail,true);assert.equal(result.inspectorInHeader,true);assert.equal(result.statusRole,'status');
  await nativePointer('mouseMoved',result.position);
  const tooltip=await evaluate(`new Promise((resolve,reject)=>{const deadline=performance.now()+3000;function check(){const tip=document.querySelector('#control-tooltip');if(!tip.hidden&&tip.textContent.includes('Session status'))return resolve({text:tip.textContent,described:document.querySelector('#source').getAttribute('aria-describedby')});if(performance.now()>deadline)return reject(new Error('Session status tooltip did not open'));requestAnimationFrame(check)}check()})`);
  assert.match(tooltip.text,/Completed/);assert.equal(tooltip.described,'control-tooltip');
  await nativeKey('Escape','Escape',27);
  assert.equal(await evaluate("document.querySelector('#control-tooltip').hidden"),true);
});

test('Live agent observation tabs expose slash states read-only and isolate session data', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    selectedSessionId='telemetry-alpha';viewMode='live';liveSteps=[];steps=[];currentStepIndex=-1;
    const observation={session:{id:'telemetry-alpha',name:'Alpha session',application:'OMP',cwd:'/alpha'},
      model:{id:'provider/test',provider:'provider',effort:'high',context_window:1000},
      context:{tokens:900,window:1000,percent:90,source:'runtime',estimated:false},
      queue:{available:true,steering:['<b>literal correction</b>'],follow_up:['Next request']},
      todos:{phases:[{name:'Build',tasks:[{content:'Ship the change',status:'in_progress'}]}]},
      jobs:{available:true,items:[{id:'build-1',name:'Build check',status:'running'}]},mode:{plan:'active'},
      approval:{status:'pending',title:'Approve the plan'}};
    onWsMessage({type:'STATE_UPDATE',event:{type:'SESSION_OBSERVATION'},session_id:'telemetry-alpha',state:{run_id:'telemetry-run',context:{session_id:'telemetry-alpha',application:'OMP'},nodes:{},edges:[],orchestrator_phase:'running',session_observation:observation}});
    await frame();
    document.querySelector('#agent-tab-commands').click();
    const commands=document.querySelector('#agent-panel-commands');
    const commandText=commands.textContent,slashes=[...commands.querySelectorAll('summary>code')].map(el=>el.textContent);
    const safe=commands.querySelector('b')===null,controls=commands.querySelectorAll('button,a').length;
    document.querySelector('#agent-tab-model').click();
    const model=document.querySelector('#agent-panel-model').textContent;
    const modelTab=document.querySelector('#agent-tab-model');modelTab.focus();modelTab.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
    const keyboard=document.activeElement.id,session=document.querySelector('#agent-panel-session').textContent;
    const warning=document.querySelector('.agent-context').dataset.level;
    onWsMessage({type:'STATE_UPDATE',event:{type:'SESSION_OBSERVATION'},session_id:'telemetry-beta',state:{run_id:'beta-run',context:{session_id:'telemetry-beta',application:'Codex'},nodes:{},edges:[],orchestrator_phase:'idle',session_observation:{session:{id:'telemetry-beta',name:'Beta'},queue:{available:false}}}});
    await frame();selectedSessionId='telemetry-beta';displaySession(liveSessions.get('telemetry-beta'));await frame();
    return{commandText,slashes,safe,controls,model,keyboard,session,warning,
      beta:document.querySelector('#agent-panel-session').textContent,
      betaQueue:document.querySelector('#agent-panel-commands').textContent,level:document.querySelector('.agent-context').dataset.level};
  })()`);
  for(const slash of ['/queue','/todo','/jobs','/ask','/compact','/retry','/memory'])assert.ok(result.slashes.includes(slash));
  assert.ok(result.commandText.includes('Next request')&&result.commandText.includes('Ship the change')&&result.commandText.includes('Build check'));
  assert.equal(result.safe,true);assert.equal(result.controls,0);assert.ok(result.model.includes('provider/test')&&result.model.includes('high'));
  assert.equal(result.keyboard,'agent-tab-session');assert.ok(result.session.includes('/alpha'));assert.equal(result.warning,'warning');
  assert.ok(result.beta.includes('telemetry-beta'));assert.ok(!result.beta.includes('/alpha')&&!result.betaQueue.includes('Next request'));
  assert.ok(result.betaQueue.includes('not expose queue snapshots'));assert.equal(result.level,'unknown');
});

test('Context ribbon clears a full warning after automatic compaction without inventing a measurement', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    selectedSessionId='context-session';viewMode='live';liveSteps=[];steps=[];currentStepIndex=-1;
    const base={run_id:'context-run',context:{session_id:'context-session',application:'OMP'},nodes:{},edges:[],orchestrator_phase:'complete'};
    const send=async obs=>{onWsMessage({type:'STATE_UPDATE',event:{type:'SESSION_OBSERVATION'},config,session_id:'context-session',state:{...base,session_observation:obs}});await frame()};
    await send({context:{tokens:960,window:1000,percent:96,source:'runtime'}});
    const originalCommand=document.querySelector('#commands-container .card'),originalHistory=steps.length;
    const full={level:document.querySelector('.agent-context').dataset.level,value:document.querySelector('.agent-context-track').getAttribute('aria-valuenow')};
    await send({context:{tokens:null,window:1000,percent:null,source:'awaiting_post_compaction_usage'},compaction:{status:'completed',at:'2026-01-01T00:00:00Z'}});
    const compacted={level:document.querySelector('.agent-context').dataset.level,value:document.querySelector('.agent-context-track').getAttribute('aria-valuenow'),note:document.querySelector('.agent-context-note').textContent};
    await send({context:{tokens:100,window:1000,percent:10,source:'runtime'}});
    const fresh={level:document.querySelector('.agent-context').dataset.level,value:document.querySelector('.agent-context-track').getAttribute('aria-valuenow')};
    return{full,compacted,fresh,retained:originalCommand===document.querySelector('#commands-container .card'),historyUnchanged:originalHistory===steps.length};
  })()`);
  assert.deepEqual(result.full,{level:'critical',value:'96'});
  assert.equal(result.compacted.level,'unknown');assert.equal(result.compacted.value,null);assert.match(result.compacted.note,/Compacted/);
  assert.deepEqual(result.fresh,{level:'normal',value:'10'});
  assert.equal(result.retained,true,'Telemetry must not recreate resource cards');assert.equal(result.historyUnchanged,true,'Telemetry must not become a tool-call step');
});

test('A repeated command exposes every call with its own arguments, outcome and keyboard selection', async () => {
  await preparePage();
  const result=await evaluate(`(async()=>{
    const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    viewMode='saved';viewRunId='repeated-command-details';selectedCallStep=null;steps=[];
    const read=commandTemplateFor('read'),write=commandTemplateFor('write'),nodes=Object.fromEntries(config.nodes.map(node=>[node.id,{...node,status:'idle'}]));
    const sequence=[{tool:read,input:'{"path":"src/first.py"}',result:'First result',success:true},{tool:write,input:'{"path":"src/other.py"}',result:'Other command',success:true},{tool:read,input:'{"path":"src/second.py"}',result:'Second failed',success:false},{tool:read,input:'{"path":"src/third.py"}'}];
    const edges=[{from:'agent-hub',to:read.id},{from:'agent-hub',to:write.id}];
    for(const [index,item] of sequence.entries()){
      nodes[item.tool.id]={...item.tool,status:'active'};
      const base={run_id:viewRunId,prompt:'Repeated command calls',orchestrator_phase:'running',nodes:structuredClone(nodes),edges,timestamp:'2026-01-01T00:00:00Z'};
      steps.push({...base,event:{type:'TOOL_CALL_START',tool:item.tool.label,call_id:'repeat-'+index,input:item.input}});
      if(item.result)steps.push({...base,event:{type:'TOOL_CALL_END',tool:item.tool.label,call_id:'repeat-'+index,result:item.result,success:item.success}});
    }
    showStep(steps.length-1);await frame();
    const wire=[...svg.querySelectorAll('g[data-edge]')].find(group=>group.dataset.edge==='agent~commands'),label=wire?.querySelector('.edge-step')?.textContent;
    const branches=[...wire.querySelectorAll('.command-branches [data-branch]')].map(path=>path.dataset.branch),card=document.getElementById(read.id).querySelector('.call-meta').textContent;
    openResourceDetails(read.id);await frame();
    const list=document.querySelector('.activity-call-list'),calls=[...list.querySelectorAll('.activity-call-item')].map(button=>button.textContent),last=document.querySelector('.activity-detail-pane').textContent;
    list.querySelector('button').click();await frame();
    const first=document.querySelector('.activity-detail-pane').textContent;
    const firstButton=list.querySelector('button');firstButton.focus();firstButton.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));await frame();
    const second=document.querySelector('.activity-detail-pane').textContent,selected=document.activeElement.dataset.step;
    const allRemain=document.querySelectorAll('.activity-call-list .activity-call-item').length;
    closeResourceDetails();return{label,branches,card,calls,last,first,second,selected,allRemain};
  })()`);
  assert.match(result.label,/#1…#4 · ×4/);assert.match(result.card,/#1…#4 · ×3/);
  assert.ok(result.branches.some(branch=>branch.includes('read'))&&result.branches.some(branch=>branch.includes('write')));
  assert.equal(result.calls.length,3);assert.ok(result.calls[0].includes('#1')&&result.calls[1].includes('#3')&&result.calls[2].includes('#4'));
  assert.ok(result.first.includes('src/first.py')&&result.first.includes('First result'));
  assert.ok(result.second.includes('src/second.py')&&result.second.includes('Second failed'));
  assert.ok(result.last.includes('src/third.py')&&!result.last.includes('First result'));assert.equal(result.selected,'4');assert.equal(result.allRemain,3);
});

test('Listening project processes use regular terminal tabs and stopping requires confirmation', async () => {
  localProcesses=[{id:'process-777',pid:777,ppid:2,pgid:777,tty:'ttys001',started_at:'fixture',name:'Local site',
    command:'node site.mjs',cwd:config.project_root,ports:[{host:'127.0.0.1',port:4321}],urls:['http://127.0.0.1:4321'],owner:'external',origin:'external_terminal',identity:'worker-identity',can_stop:true}];
  try{
    await preparePage();
    const before=requests.filter(row=>row.url==='/api/local-processes/process-777/stop').length;
    const result=await evaluate(`(async()=>{
      await serviceTerminal.refresh();serviceTerminal.onOpen();
      const text=document.querySelector('#terminal-view').textContent,original=window.confirm;
      try{
        window.confirm=()=>false;document.querySelector('.terminal-service-toolbar [data-action="stop"]').click();
        const afterCancel=serviceTerminal.processes.length;
        window.confirm=()=>true;document.querySelector('.terminal-service-toolbar [data-action="stop"]').click();
        for(let attempt=0;attempt<200&&serviceTerminal.pendingAction.size;attempt++)await new Promise(resolve=>setTimeout(resolve,10));
        return{text,afterCancel,remaining:serviceTerminal.processes.length,visible:document.body.classList.contains('terminal-active'),panel:!!document.querySelector('.terminal-process-panel'),tabs:[...document.querySelectorAll('.terminal-subtab')].map(el=>el.textContent),status:serviceTerminal.services.find(item=>item.id==='process-777').status};
      }finally{window.confirm=original}
    })()`);
    assert.match(result.text,/Local site/);assert.match(result.text,/777/);assert.match(result.text,/node site.mjs/);assert.match(result.text,/4321/);
    assert.equal(result.afterCancel,1);assert.equal(result.remaining,0);assert.equal(result.visible,true);
    assert.equal(result.panel,false);assert.equal(result.tabs.length,1);assert.equal(result.status,'stopped');
    assert.equal(requests.filter(row=>row.url==='/api/local-processes/process-777/stop').length,before+1);
  }finally{localProcesses=[]}
});

test('Memory availability remains distinct from observed activity after Russian localization',async()=>{
  await preparePage();
  const result=await evaluate(`(async()=>{
    const language=ObserverI18n.language,node=config.nodes.find(node=>node.kind==='memory');
    try{
      ObserverI18n.setLanguage('ru');
      config.memory_systems=[{id:node.memory_system_id,name:'OMP · Hindsight',enabled:true,endpoint:'http://127.0.0.1:18789',categories:[]}];
      memoryView.setCatalog(false);
      memoryView.snapshots.set(node.memory_system_id,{status:'available',banks:[{id:'omp',stats:{total_nodes:123,pending_operations:0,failed_operations:7}}]});
      memoryView.updateCards();await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
      const card=document.getElementById(node.id),enabled={badge:card.querySelector('.card-status').textContent,state:card.querySelector('.memory-state').textContent,facts:card.querySelector('.memory-card-taxonomy').textContent};
      config.memory_systems[0].enabled=false;memoryView.updateCards();await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
      return{enabled,disabled:card.querySelector('.card-status').textContent,disabledNote:card.querySelector('.memory-card-taxonomy').textContent};
    }finally{ObserverI18n.setLanguage(language)}
  })()`);
  assert.match(result.enabled.badge,/Включено/);assert.match(result.enabled.badge,/API доступен/);
  assert.match(result.enabled.state,/Не наблюдалось/);assert.match(result.enabled.facts,/123/);assert.match(result.enabled.facts,/7/);
  assert.equal(result.disabled,'Отключено');assert.match(result.disabledNote,/не выбрана/);
});

test('Switching Codex to OMP in one folder inspects the newly selected memory backend',async()=>{
  memorySystems=[{id:'demo',status:'available',banks:[{id:'omp',stats:{total_nodes:123,pending_operations:0,failed_operations:0}}]}];
  sessionFixtures=new Map([[config.project_root,[
    {session_id:'memory-codex',session_name:'Codex source',application:'Codex',status:'idle',config},
    {session_id:'memory-omp',session_name:'OMP source',application:'OMP',status:'idle',
      config:{...config,memory_systems:[{id:'demo',name:'Hindsight',enabled:true,endpoint:'http://127.0.0.1:18789',categories:[]}]}}
  ]]]);
  try{
    await preparePage();
    const result=await evaluate(`(async()=>{
      await refreshSessions();await selectSession('memory-codex');await selectSession('memory-omp');
      for(let attempt=0;attempt<200&&memoryView.loading;attempt++)await new Promise(resolve=>setTimeout(resolve,10));
      const card=document.querySelector('#memory_demo');
      return{application:state.context?.application,badge:card.querySelector('.card-status').textContent,facts:card.querySelector('.memory-card-taxonomy').textContent,error:memoryView.error};
    })()`);
    assert.equal(result.application,'OMP');assert.match(result.badge,/API available/);assert.match(result.facts,/123/);assert.equal(result.error,'');
  }finally{sessionFixtures=null;memorySystems=[]}
});

test('A registered localhost keeps one terminal tab and its real logs instead of a duplicate process tab',async()=>{
  localServices=[{id:'browser-fixture',name:'Site',endpoint:'http://127.0.0.1:4321',endpoints:['http://127.0.0.1:4321'],status:'running',connected:true,owner:'external',can_start:false,can_stop:false,log_available:true}];
  localProcesses=[{id:'process-778',pid:778,ppid:2,name:'node',command:'node site.mjs',cwd:config.project_root,ports:[{host:'127.0.0.1',port:4321}],urls:['http://127.0.0.1:4321'],identity:'site-identity',owner:'external',can_stop:true}];
  try{
    await preparePage();
    const result=await evaluate(`(async()=>{
      await serviceTerminal.refresh();serviceTerminal.openService('browser-fixture');
      for(let attempt=0;attempt<200&&serviceTerminal.inFlightLogsFetch;attempt++)await new Promise(resolve=>setTimeout(resolve,10));
      return{tabs:document.querySelectorAll('.terminal-subtab').length,text:document.querySelector('.terminal-service-logs').textContent,images:document.querySelector('.terminal-service-logs').querySelectorAll('img').length};
    })()`);
    assert.equal(result.tabs,1);assert.match(result.text,/PID 778/);assert.match(result.text,/node site.mjs/);assert.match(result.text,/service output/);assert.equal(result.images,0);
  }finally{localServices=[];localProcesses=[]}
});

test('Terminal follows light and dark graph themes without replacing the tab or losing logs',async()=>{
  localServices=[{id:'browser-fixture',name:'Theme site',status:'running',connected:true,owner:'graph',can_start:false,can_stop:false,log_available:true}];
  try{
    await preparePage();
    const result=await evaluate(`(async()=>{
      await serviceTerminal.refresh();serviceTerminal.openService('browser-fixture');
      for(let attempt=0;attempt<200&&serviceTerminal.inFlightLogsFetch;attempt++)await new Promise(resolve=>setTimeout(resolve,10));
      const original=document.documentElement.dataset.theme,tab=document.querySelector('.terminal-subtab'),text=document.querySelector('.terminal-service-logs').textContent;
      const samples=[];
      try{
        for(const theme of ['light','dark']){
          if(document.documentElement.dataset.theme!==theme)document.querySelector('#theme-toggle').click();
          await new Promise(requestAnimationFrame);
          samples.push({theme,background:getComputedStyle(document.querySelector('.terminal-body')).backgroundColor,foreground:getComputedStyle(document.querySelector('.terminal-line.stream-stderr')).color});
        }
        return{samples,sameTab:tab===document.querySelector('.terminal-subtab'),sameLogs:text===document.querySelector('.terminal-service-logs').textContent};
      }finally{if(document.documentElement.dataset.theme!==original)document.querySelector('#theme-toggle').click()}
    })()`);
    const channels=color=>color.match(/[0-9.]+/g).slice(0,3).map(Number);
    assert.ok(channels(result.samples[0].background).every(value=>value>230));assert.ok(channels(result.samples[1].background).every(value=>value<40));
    assert.notEqual(result.samples[0].foreground,result.samples[1].foreground);assert.equal(result.sameTab,true);assert.equal(result.sameLogs,true);
  }finally{localServices=[]}
});
