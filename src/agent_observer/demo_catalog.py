"""Synthetic showcase catalog: no project files, history, credentials or services."""
def demo_bootstrap():
    nodes = [
        {'id':'demo-guide','kind':'document','label':'DesignGuide.md','path':'docs/knowledge/DesignGuide.md','group':'Knowledge'},
        {'id':'demo-lessons','kind':'document','label':'FailureLessons.md','path':'docs/knowledge/FailureLessons.md','group':'Knowledge'},
        {'id':'demo-build','kind':'tool','label':'OpenSCAD CLI','match':{'command_prefixes':['openscad']}},
        {'id':'demo-check','kind':'tool','label':'Mesh verifier','match':{'tool_names':['demo_mesh_check']}},
        {'id':'demo-mcp','kind':'mcp','name':'demo-inspector','label':'demo-inspector','tool_names':['mcp__demo__inspect'],'connection':'configured'},
        {'id':'demo-skill','kind':'skill','name':'demo-design','label':'demo-design','path':'skill://demo-design'},
        {'id':'demo-memory','kind':'memory','label':'Demo memory','memory_system_id':'demo-memory','provider':'hindsight','bank_ids':['demo']},
    ]
    config = {'project_root':'/__observer_demo__','project_title':'DEMO · simulated project','agent_label':'CAD agent · DEMO',
              'agent_runtime':'cad','nodes':nodes,'native_tools':[{'name':name} for name in ['read','write','edit','bash','demo_memory_recall','demo_memory_retain']],
              'memory_systems':[],'document_links':[],'agent_spec':[],'hidden_blocks':[],'block_layouts':{},'observed_only':True}
    return {'config':config,'runs':[],'history':[],'state':{'nodes':{},'edges':[],'orchestrator_phase':'idle'},'token':''}
