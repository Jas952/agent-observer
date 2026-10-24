"""Replay fictional telemetry only. No listed project commands are executed."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_observer.observer_client import ObserverClient


def main():
    config = Path(__file__).resolve().parents[1] / "config/observer.project.json"
    client = ObserverClient(config_file=config, timeout=1)
    run = client.run(
        "Add a friendly greeting to the sample dashboard and verify the result.",
        mode="Synthetic demo · no commands executed",
        project_spec="Use fictional data. Keep the greeting small, readable and tested.",
        comments=[{"label": "Refinement note", "text": "Use Hello, Demo! in the preview."}],
    )

    def emit(kind, **fields):
        if not run.event(kind, **fields):
            raise RuntimeError(f"Start Observer on port 8766 first ({client.last_error}).")
        time.sleep(0.18)

    emit("PROMPT_START", prompt=run.prompt, project=run.project, **run.metadata)
    emit("THINKING", phase="DEMO · inspect sample files and requirements")
    calls = [
        ("glob", {"pattern": "src/*.py"}, "src/greeting.py"),
        ("read", {"path": "docs/requirements.md"}, "Demo requirements loaded"),
        ("read", {"path": "docs/architecture.md"}, "Demo architecture loaded"),
        ("read_image", {"path": "assets/preview.svg"}, "Fictional greeting preview inspected"),
        ("grep", {"pattern": "greeting", "path": "src/greeting.py"}, "Found greeting(name)"),
        ("write", {"path": "src/message.txt", "content": "Hello, Demo!"}, "Demo output prepared"),
        ("edit", {"path": "src/greeting.py", "old": "World", "new": "Demo"}, "Demo default name updated"),
        ("apply_patch", "*** Begin Patch\n*** Update File: src/greeting.py\n@@\n-    return f\"Hello, {name}!\"\n+    return f\"Hello, {name}!\"\n*** End Patch", "Illustrative patch recorded"),
        ("bash", "python3 src/greeting.py", "Hello, Demo!"),
        ("python3", "python3 -m compileall src/greeting.py", "Synthetic check passed"),
    ]
    for index, (tool, arguments, result) in enumerate(calls):
        import json
        call_id = f"demo-call-{index}"
        emit("TOOL_CALL_START", tool=tool, call_id=call_id,
             input=arguments if isinstance(arguments, str) else json.dumps(arguments))
        emit("TOOL_CALL_END", tool=tool, call_id=call_id, success=True,
             result=f"DEMO ONLY · {result}", duration_ms=180)
        if tool == "read":
            emit("KNOWLEDGE_ACCESS", path=arguments["path"], success=True,
                 reason="DEMO ONLY · simulated document read", estimated_tokens=120)
    emit("THINKING", phase="DEMO · sample refinement complete")
    emit("GENERATION_END", success=True, status="completed",
         result="DEMO ONLY · Hello, Demo! prepared. Sample checks passed. No commands were executed.")
    print("Synthetic demo complete: 2 documents, 10 calls, final result. Open http://127.0.0.1:8766/")


if __name__ == "__main__":
    main()
