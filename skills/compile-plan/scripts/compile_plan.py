#!/usr/bin/env python3
"""Compile a TOML plan document into deterministic sd-based scripts.

Parses Before/After content from TOML plan files per the Compilable Plan Spec v2
and generates per-task scripts that apply changes via `sd -F` (fixed-string
replacement).

Also supports legacy markdown (.md) plans with a deprecation warning.

Usage:
    python3 compile_plan.py <plan-path> [--dry-run] [--output-dir <dir>]

Output:
    compiled/          (sibling to plan file, or --output-dir)
    ├── manifest.json
    ├── TASK-1.sh
    ├── TASK-1.py
    └── ...
"""

import argparse
import base64
import json
import re
import stat
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# TOML parsing
# ---------------------------------------------------------------------------

VALID_TYPES = {"replace", "create", "delete"}
VALID_ID_RE = re.compile(r"^(TASK|Issue|Fix|FIX)-\d+$", re.IGNORECASE)


def strip_toml_newlines(s: str | None) -> str | None:
    """Strip the leading newline that TOML adds after opening triple-quotes."""
    if s is None:
        return None
    # TOML multiline literal strings: first newline after ''' is stripped by
    # the parser, but basic multiline strings (""") may retain one. Normalise.
    if s.startswith("\n"):
        s = s[1:]
    if s.endswith("\n"):
        s = s[:-1]
    return s


def infer_type(change: dict) -> str:
    has_before = change.get("before") is not None
    has_after = change.get("after") is not None
    if has_before and has_after:
        return "replace"
    if not has_before and has_after:
        return "create"
    if has_before and not has_after:
        return "delete"
    return "manual"


def parse_toml_plan(text: str) -> list[dict]:
    """Parse a TOML plan into a list of task dicts."""
    if tomllib is None:
        print("Error: Python 3.11+ required (tomllib), or install 'tomli'.",
              file=sys.stderr)
        sys.exit(1)

    data = tomllib.loads(text)
    tasks_section = data.get("tasks", {})
    if not tasks_section:
        return []

    tasks = []
    for task_id, task_data in tasks_section.items():
        if not VALID_ID_RE.match(task_id):
            print(f"Warning: task ID '{task_id}' does not match expected "
                  f"pattern (TASK-N, Issue-N, Fix-N)", file=sys.stderr)

        description = task_data.get("description", "")
        acceptance = task_data.get("acceptance", [])
        raw_changes = task_data.get("changes", [])

        changes = []
        for c in raw_changes:
            file_path = c.get("file")
            if not file_path:
                print(f"Error: {task_id} has a change without a 'file' field.",
                      file=sys.stderr)
                sys.exit(1)
            changes.append({
                "before": strip_toml_newlines(c.get("before")),
                "after": strip_toml_newlines(c.get("after")),
                "file": file_path,
            })

        # Determine type
        explicit_type = task_data.get("type")
        if explicit_type:
            task_type = explicit_type.lower()
            if task_type not in VALID_TYPES:
                print(f"Error: {task_id} has invalid type '{task_type}'.",
                      file=sys.stderr)
                sys.exit(1)
        elif changes:
            task_type = infer_type(changes[0])
        else:
            task_type = "manual"

        # Primary file (first change's file, for manifest)
        file_path = changes[0]["file"] if changes else None

        tasks.append({
            "id": task_id,
            "description": description,
            "file": file_path,
            "type": task_type,
            "changes": changes,
            "acceptance": acceptance,
        })

    return tasks


# ---------------------------------------------------------------------------
# Legacy markdown parsing (deprecated)
# ---------------------------------------------------------------------------

HEADER_RE = re.compile(
    r"#{2,4}\s+(TASK-[^\s:]+|Issue-[^\s:]+|Fix-[^\s:]+|FIX-[^\s:]+):\s*(.*)",
    re.IGNORECASE,
)

FILE_RE = re.compile(r"\*\*File:\*\*\s*`([^`]+)`", re.IGNORECASE)
TYPE_RE = re.compile(r"\*\*Type:\*\*\s*(\w+)", re.IGNORECASE)

LABELLED_BLOCK_RE = re.compile(
    r"\*\*(?P<label>Before|After):\*\*[^\n]*\n"
    r"(?:[ \t]*\n)*"
    r"```\w*\n"
    r"(?P<code>.*?)"
    r"\n```",
    re.DOTALL | re.IGNORECASE,
)

CHANGE_HEADER_RE = re.compile(
    r"\*\*Change\s+(\d+):\*\*", re.IGNORECASE
)

ACCEPTANCE_RE = re.compile(
    r"\*\*(?:Acceptance|Verification|Verify):\*\*\s*(.*)",
    re.IGNORECASE,
)


def split_sections(text: str) -> list[str]:
    return re.split(r"^\s*---\s*$", text, flags=re.MULTILINE)


def _extract_single_change_md(section: str) -> list[dict]:
    blocks = {}
    file_path = None

    file_match = FILE_RE.search(section)
    if file_match:
        file_path = file_match.group(1).strip()

    for m in LABELLED_BLOCK_RE.finditer(section):
        label = m.group("label").lower()
        blocks[label] = m.group("code")

    if not blocks:
        return []

    return [{
        "before": blocks.get("before"),
        "after": blocks.get("after"),
        "file": file_path,
    }]


def _extract_multi_change_md(section: str, headers: list) -> list[dict]:
    changes = []
    top_file_match = FILE_RE.search(section[:headers[0].start()])
    top_file = top_file_match.group(1).strip() if top_file_match else None

    # Validate: reject ambiguous multi-file declarations in top-level File field
    if top_file:
        file_line_match = re.search(
            r'\*\*File:\*\*\s*(.*)', section[:headers[0].start()])
        if file_line_match:
            paths_on_line = re.findall(r'`([^`]+)`', file_line_match.group(1))
            if len(paths_on_line) > 1:
                hdr_match = HEADER_RE.search(section)
                task_name = hdr_match.group(1) if hdr_match else "unknown"
                print(f"ERROR: {task_name} has multiple files in one "
                      f"**File:** field: {paths_on_line}", file=sys.stderr)
                print(f"  Each sub-change must have its own **File:** field. "
                      f"See compilable-plan-spec.md 'Multi-Change Tasks'.",
                      file=sys.stderr)
                sys.exit(1)

    for i, hdr in enumerate(headers):
        start = hdr.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(section)
        subsection = section[start:end]

        blocks = {}
        file_match = FILE_RE.search(subsection)
        file_path = file_match.group(1).strip() if file_match else top_file

        for m in LABELLED_BLOCK_RE.finditer(subsection):
            label = m.group("label").lower()
            blocks[label] = m.group("code")

        if blocks:
            changes.append({
                "before": blocks.get("before"),
                "after": blocks.get("after"),
                "file": file_path,
            })

    return changes


def extract_blocks_md(section: str) -> list[dict]:
    change_headers = list(CHANGE_HEADER_RE.finditer(section))
    if change_headers:
        return _extract_multi_change_md(section, change_headers)
    return _extract_single_change_md(section)


def extract_acceptance_md(section: str) -> list[str]:
    match = ACCEPTANCE_RE.search(section)
    if not match:
        return []
    rest = section[match.start():]
    commands = re.findall(r"`([^`]+)`", rest)
    cmd_prefixes = ("gfortran ", "make ", "cmake ", "fpm ", "git ", "sd ", "python", "bash ", "sh ")
    return [c for c in commands if any(c.startswith(p) for p in cmd_prefixes)]


def parse_task_md(section: str) -> dict | None:
    header = HEADER_RE.search(section)
    if not header:
        return None

    task_id = header.group(1)
    description = header.group(2).strip()

    type_match = TYPE_RE.search(section)
    explicit_type = type_match.group(1).lower() if type_match else None

    changes = extract_blocks_md(section)

    if explicit_type:
        task_type = explicit_type
    elif not changes:
        task_type = "manual"
    else:
        first = changes[0]
        if first["before"] and first["after"]:
            task_type = "replace"
        elif not first["before"] and first["after"]:
            task_type = "create"
        elif first["before"] and not first["after"]:
            task_type = "delete"
        else:
            task_type = "manual"

    file_path = None
    if changes:
        file_path = changes[0].get("file")
    if not file_path:
        file_match = FILE_RE.search(section)
        if file_match:
            file_path = file_match.group(1).strip()

    acceptance = extract_acceptance_md(section)

    return {
        "id": task_id,
        "description": description,
        "file": file_path,
        "type": task_type,
        "changes": changes,
        "acceptance": acceptance,
    }


def parse_md_plan(text: str) -> list[dict]:
    sections = split_sections(text)
    tasks = []
    for section in sections:
        task = parse_task_md(section)
        if task:
            tasks.append(task)
    return tasks


# ---------------------------------------------------------------------------
# Code generation — Python runners with base64-encoded content
# ---------------------------------------------------------------------------

def b64(content: str) -> str:
    return base64.b64encode(content.encode()).decode()


def generate_replace_py(task: dict) -> str:
    steps = []
    for i, change in enumerate(task["changes"]):
        before_b64 = b64(change["before"])
        after_b64 = b64(change["after"])
        target = change.get("file") or task["file"]
        steps.append({
            "before_b64": before_b64,
            "after_b64": after_b64,
            "target": target,
            "index": i,
        })

    steps_json = json.dumps(steps)
    return textwrap.dedent(f'''\
        #!/usr/bin/env python3
        """{task["id"]}: {task["description"]}"""
        import base64, json, subprocess, sys

        TASK_ID = "{task["id"]}"
        STEPS = json.loads({steps_json!r})

        for step in STEPS:
            before = base64.b64decode(step["before_b64"]).decode()
            after = base64.b64decode(step["after_b64"]).decode()
            target = step["target"]
            idx = step["index"]

            content = open(target).read()
            if before not in content:
                print(f"FAILED {{TASK_ID}} change {{idx}}: pattern not found in {{target}}", file=sys.stderr)
                print(f"Expected (first 200 chars): {{repr(before[:200])}}", file=sys.stderr)
                sys.exit(1)

            result = subprocess.run(
                ["sd", "-F", "-A", "-n", "1", before, after, target],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"FAILED {{TASK_ID}} change {{idx}}: sd error: {{result.stderr}}", file=sys.stderr)
                sys.exit(result.returncode)

            new_content = open(target).read()
            if after and after not in new_content:
                print(f"FAILED {{TASK_ID}} change {{idx}}: replacement not found after apply", file=sys.stderr)
                sys.exit(1)

            print(f"OK {{TASK_ID}} change {{idx}}: applied to {{target}}")

        print(f"OK {{TASK_ID}}: all changes applied")
    ''')


def generate_delete_py(task: dict) -> str:
    change = task["changes"][0]
    before_b64 = b64(change["before"])
    target = change.get("file") or task["file"]
    return textwrap.dedent(f'''\
        #!/usr/bin/env python3
        """{task["id"]}: {task["description"]}"""
        import base64, subprocess, sys

        BEFORE = base64.b64decode("{before_b64}").decode()
        TARGET = "{target}"
        TASK_ID = "{task["id"]}"

        content = open(TARGET).read()
        if BEFORE not in content:
            print(f"FAILED {{TASK_ID}}: pattern not found in {{TARGET}}", file=sys.stderr)
            print(f"Expected (first 200 chars): {{repr(BEFORE[:200])}}", file=sys.stderr)
            sys.exit(1)

        result = subprocess.run(
            ["sd", "-F", "-A", "-n", "1", BEFORE, "", TARGET],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"FAILED {{TASK_ID}}: sd error: {{result.stderr}}", file=sys.stderr)
            sys.exit(result.returncode)

        new_content = open(TARGET).read()
        if BEFORE in new_content:
            print(f"FAILED {{TASK_ID}}: pattern still present after delete", file=sys.stderr)
            sys.exit(1)

        print(f"OK {{TASK_ID}}: deleted block from {{TARGET}}")
    ''')


def generate_create_py(task: dict) -> str:
    change = task["changes"][0]
    content_b64 = b64(change["after"])
    target = change.get("file") or task["file"]
    return textwrap.dedent(f'''\
        #!/usr/bin/env python3
        """{task["id"]}: {task["description"]}"""
        import base64, sys
        from pathlib import Path

        CONTENT = base64.b64decode("{content_b64}").decode()
        TARGET = "{target}"
        TASK_ID = "{task["id"]}"

        target_path = Path(TARGET)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(CONTENT)

        if not target_path.exists():
            print(f"FAILED {{TASK_ID}}: file not created at {{TARGET}}", file=sys.stderr)
            sys.exit(1)

        print(f"OK {{TASK_ID}}: created {{TARGET}}")
    ''')


def generate_task_sh(task: dict, plan_path: str) -> str:
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        # {task["id"]}: {task["description"]}
        # Source: {plan_path}
        # Type: {task["type"]}
        # File: {task["file"] or "N/A"}
        python3 "$(dirname "$0")/{task["id"]}.py"
    """)


GENERATORS = {
    "replace": generate_replace_py,
    "delete": generate_delete_py,
    "create": generate_create_py,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compile plan documents into deterministic sd-based scripts.",
    )
    parser.add_argument("plan", help="Path to the plan document (.toml or .md)")
    parser.add_argument("--output-dir", "-o",
                        help="Output directory (default: compiled/ sibling to plan)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report without generating scripts")
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    if not plan_path.exists():
        print(f"Error: plan file not found: {plan_path}", file=sys.stderr)
        sys.exit(1)

    text = plan_path.read_text()

    # Dispatch based on file extension
    if plan_path.suffix == ".toml":
        tasks = parse_toml_plan(text)
    elif plan_path.suffix == ".md":
        print("WARNING: Markdown plan format is deprecated. "
              "Migrate to TOML format. See compilable-plan-spec.md.",
              file=sys.stderr)
        tasks = parse_md_plan(text)
    else:
        # Try TOML first, fall back to markdown
        try:
            tasks = parse_toml_plan(text)
        except Exception:
            tasks = parse_md_plan(text)

    if not tasks:
        print("No compilable tasks found in plan.", file=sys.stderr)
        sys.exit(1)

    compiled = [t for t in tasks if t["type"] != "manual"]
    manual = [t for t in tasks if t["type"] == "manual"]

    print(f"Parsed {len(tasks)} tasks: "
          f"{len(compiled)} compilable, {len(manual)} manual")
    for t in tasks:
        n_changes = len(t["changes"])
        extra = f" ({n_changes} changes)" if n_changes > 1 else ""
        status = "SKIP (manual)" if t["type"] == "manual" else t["type"]
        print(f"  {t['id']}: {status}{extra} — {t['description'][:60]}")

    if args.dry_run:
        return

    output_dir = (Path(args.output_dir).resolve()
                  if args.output_dir else plan_path.parent / "compiled")
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_tasks = []
    skipped = []

    for task in tasks:
        if task["type"] == "manual":
            skipped.append({
                "id": task["id"],
                "reason": "no parseable Before/After code blocks",
                "description": task["description"],
            })
            continue

        if not task["file"] and task["type"] != "create":
            skipped.append({
                "id": task["id"],
                "reason": "no file path found",
                "description": task["description"],
            })
            continue

        generator = GENERATORS.get(task["type"])
        if not generator:
            skipped.append({
                "id": task["id"],
                "reason": f"unknown type: {task['type']}",
                "description": task["description"],
            })
            continue

        py_content = generator(task)
        py_path = output_dir / f"{task['id']}.py"
        py_path.write_text(py_content)

        sh_content = generate_task_sh(task, str(plan_path))
        sh_path = output_dir / f"{task['id']}.sh"
        sh_path.write_text(sh_content)
        sh_path.chmod(sh_path.stat().st_mode | stat.S_IEXEC)

        manifest_tasks.append({
            "id": task["id"],
            "script": f"{task['id']}.sh",
            "runner": f"{task['id']}.py",
            "file": task["file"],
            "type": task["type"],
            "changes": len(task["changes"]),
            "description": task["description"],
            "acceptance": task["acceptance"],
        })

    manifest = {
        "plan": str(plan_path),
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "tasks": manifest_tasks,
        "skipped": skipped,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"\nCompiled {len(manifest_tasks)} tasks to {output_dir}/")
    if skipped:
        print(f"Skipped {len(skipped)} tasks:")
        for s in skipped:
            print(f"  {s['id']}: {s['reason']}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
