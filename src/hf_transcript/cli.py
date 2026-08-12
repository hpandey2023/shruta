from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .pipeline import iter_captures, process_capture, sweep_downloads


def _add_processing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", type=Path, required=True, help="Transcript workspace")
    parser.add_argument("--scope", default="transcripts", help="Retrieval scope in frontmatter")
    parser.add_argument(
        "--agent",
        choices=("none", "codex", "claude", "openai-compatible"),
        default="none",
        help="Optional agent used for cited insight notes",
    )
    parser.add_argument("--model", help="Agent model override")
    parser.add_argument("--reasoning", default="medium", help="Codex reasoning effort")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("--language", default="English", help="Insight-note language")
    parser.add_argument("--force", action="store_true", help="Regenerate existing outputs")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hf-transcript")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process", help="Process one capture or a raw directory")
    process.add_argument("input", type=Path)
    _add_processing_options(process)

    sweep = subparsers.add_parser("sweep", help="Move new Chrome downloads and process them")
    sweep.add_argument(
        "--downloads", type=Path, default=Path("~/Downloads/hf-transcripts"), help="Capture relay"
    )
    sweep.add_argument("--raw-dir", type=Path, help="Raw destination (defaults to workspace/raw)")
    _add_processing_options(sweep)

    doctor = subparsers.add_parser("doctor", help="Check local prerequisites")
    doctor.add_argument("--workspace", type=Path, required=True)
    doctor.add_argument("--agent", choices=("none", "codex", "claude"), default="none")
    return parser


def _process_paths(paths: list[Path], args: argparse.Namespace) -> int:
    failed = False
    for path in paths:
        result = process_capture(
            path,
            workspace=args.workspace,
            scope=args.scope,
            agent_provider=args.agent,
            agent_model=args.model,
            agent_reasoning=args.reasoning,
            agent_base_url=args.base_url,
            language=args.language,
            force=args.force,
        )
        print(
            json.dumps(
                {
                    "input": path.name,
                    "status": result.status,
                    "evidence": str(result.evidence_path) if result.evidence_path else None,
                    "insights": str(result.insights_path) if result.insights_path else None,
                    "warnings": result.warnings,
                },
                ensure_ascii=False,
            )
        )
        failed = failed or result.status == "failed"
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        workspace = args.workspace.expanduser()
        checks = {
            "workspace_parent_exists": workspace.parent.exists(),
            "agent": args.agent,
            "agent_available": args.agent == "none" or bool(shutil.which(args.agent)),
        }
        print(json.dumps(checks, indent=2))
        return (
            0
            if all(value for key, value in checks.items() if key.endswith(("exists", "available")))
            else 1
        )

    if args.command == "process":
        source = args.input.expanduser()
        paths = list(iter_captures(source)) if source.is_dir() else [source]
        return _process_paths(paths, args)

    raw_dir = (args.raw_dir or (args.workspace / "raw")).expanduser()
    moved = sweep_downloads(args.downloads, raw_dir)
    print(json.dumps({"moved": [path.name for path in moved]}, ensure_ascii=False))
    return _process_paths(moved, args) if moved else 0


if __name__ == "__main__":
    sys.exit(main())
