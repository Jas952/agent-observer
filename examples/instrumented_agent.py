"""Example integration; replace the body with the project's real operations."""
from pathlib import Path
from agent_observer.observer_client import ObserverClient

def handle_request(project_root, config_file, prompt):
    observer = ObserverClient(project_root=project_root, config_file=config_file)
    with observer.run(prompt, mode="Request processing") as run:
        run.phase("Reading project instructions")
        path = Path(project_root) / "README.md"
        with run.tool("read", {"path": "README.md"}) as call:
            content = path.read_text(encoding="utf-8")
            call["result"] = f"Read {len(content)} characters"
        run.document("README.md", estimated_tokens=round(len(content) / 3.5))
        # Execute the actual agent here and wrap its tool callbacks in run.tool().
        # Report provider usage once per model response: run.usage(input, output).
        run.result = "Project instructions loaded"
        return content
