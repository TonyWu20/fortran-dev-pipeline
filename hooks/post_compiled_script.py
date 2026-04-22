#!/usr/bin/env python3
"""
PostToolUse hook: detect compiled script execution and force agent stop.

When an implementation-executor subagent runs a compiled script via Bash,
this hook fires and returns {"decision": "block"} to stop the agent
immediately. The SubagentStop hook (verify_impl_task.py) then handles
verification, checkpointing, and committing.

Trigger: PostToolUse with matcher "Bash"
"""

import json
import re
import sys


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_output = data.get("tool_output", "")

    if tool_name != "Bash":
        sys.exit(0)

    command = tool_input.get("command", "")

    # Match compiled script execution patterns
    if not re.search(r"compiled/TASK-\d+\.sh", command):
        sys.exit(0)

    # Parse exit code from tool output if available
    # Tool output typically ends with "Exit code: N" or similar
    exit_code = 0
    if "exit code" in tool_output.lower():
        m = re.search(r"exit code[:\s]+(\d+)", tool_output, re.IGNORECASE)
        if m:
            exit_code = int(m.group(1))

    # Extract task ID from command
    task_match = re.search(r"(TASK-\d+)", command)
    task_id = task_match.group(1) if task_match else "unknown"

    if exit_code == 0:
        reason = (
            f"Compiled script for {task_id} executed successfully (exit 0). "
            f"Task execution complete — stopping agent for SubagentStop verification hook."
        )
    else:
        reason = (
            f"Compiled script for {task_id} failed (exit {exit_code}). "
            f"Stopping agent — SubagentStop hook will record the failure."
        )

    output = {"decision": "block", "reason": reason}
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
