#!/usr/bin/env bash
# task-sidecar.sh — Read compiled manifest and write verification sidecars
#
# The compiled manifest (manifest.json) is the single source of truth for
# task IDs, descriptions, and acceptance commands. This script reads from
# it instead of parsing plan documents directly.
#
# Usage:
#   task-sidecar.sh list    <manifest>                    List all task IDs
#   task-sidecar.sh prepare <manifest> <id> [--out path]  Write sidecar JSON for verification hook

set -euo pipefail

usage() {
	cat >&2 <<'EOF'
Usage:
  task-sidecar.sh list    <manifest.json>                    List all task IDs
  task-sidecar.sh prepare <manifest.json> <id> [--out path]  Write sidecar JSON for verification hook
EOF
	exit 1
}

[[ $# -ge 2 ]] || usage

cmd="$1"
manifest="$2"

[[ -f "$manifest" ]] || {
	echo "Error: manifest not found: $manifest" >&2
	exit 1
}

case "$cmd" in
list)
	python3 -c '
import json, sys
manifest = json.load(open(sys.argv[1]))
for task in manifest["tasks"]:
    print(task["id"])
' "$manifest"
	;;

prepare)
	[[ $# -ge 3 ]] || usage
	task_id="$3"
	out_path="${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/current_task_${task_id}.json"

	# Parse --out flag
	shift 3
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--out)
			out_path="$2"
			shift 2
			;;
		*)
			shift
			;;
		esac
	done

	mkdir -p "$(dirname "$out_path")"

	python3 -c '
import json, sys
from pathlib import Path
from datetime import datetime, timezone

manifest_path = sys.argv[1]
task_id = sys.argv[2]
out_path = sys.argv[3]

manifest = json.load(open(manifest_path))

task = None
for t in manifest["tasks"]:
    if t["id"] == task_id:
        task = t
        break

if task is None:
    print(f"Error: task {task_id} not found in manifest", file=sys.stderr)
    sys.exit(1)

plan_path = manifest.get("plan", "")
plan_slug = Path(plan_path).stem.replace("_", "-").lower()

all_task_ids = [t["id"] for t in manifest["tasks"]]

data = {
    "task_id": task["id"],
    "task_description": task.get("description", ""),
    "plan_path": plan_path,
    "plan_slug": plan_slug,
    "acceptance_commands": task.get("acceptance", []),
    "acceptance_prose": [],
    "all_task_ids": all_task_ids,
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}

Path(out_path).write_text(json.dumps(data, indent=2) + "\n")
print(json.dumps(data, indent=2))
' "$manifest" "$task_id" "$out_path"

	echo "Wrote sidecar to $out_path" >&2
	;;

*)
	echo "Unknown command: $cmd" >&2
	usage
	;;
esac
