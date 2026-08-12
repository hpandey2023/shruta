from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


class AgentError(RuntimeError):
    pass


def _strip_fence(text: str) -> str:
    output = text.strip()
    if output.startswith("```"):
        first_newline = output.find("\n")
        output = output[first_newline + 1 :] if first_newline >= 0 else ""
        if output.rstrip().endswith("```"):
            output = output.rstrip()[:-3]
    return output.strip()


def run_codex(prompt: str, *, model: str, reasoning: str, timeout: int = 1200) -> str:
    binary = os.environ.get("CODEX_BIN") or shutil.which("codex")
    if not binary:
        raise AgentError("Codex CLI was not found")
    work_dir = Path(
        os.environ.get("HF_TRANSCRIPT_AGENT_WORK", "~/.cache/hf-transcript/agent-work")
    ).expanduser()
    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="hf-transcript-", suffix=".md", delete=False) as handle:
        output_path = Path(handle.name)
    command = [
        binary,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning}"',
        "-C",
        str(work_dir),
        "-o",
        str(output_path),
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            error = (result.stderr or result.stdout or "unknown failure").strip()
            raise AgentError(f"Codex exited {result.returncode}: {error[-800:]}")
        output = output_path.read_text(encoding="utf-8", errors="replace").strip()
        if not output:
            raise AgentError("Codex returned an empty response")
        return _strip_fence(output)
    finally:
        output_path.unlink(missing_ok=True)


def run_claude(prompt: str, *, model: str, timeout: int = 1200) -> str:
    binary = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not binary:
        raise AgentError("Claude CLI was not found")
    result = subprocess.run(
        [binary, "-p", "--model", model, "--allowedTools", ""],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        error = (result.stderr or result.stdout or "unknown failure").strip()
        raise AgentError(f"Claude exited {result.returncode}: {error[-800:]}")
    if not (result.stdout or "").strip():
        raise AgentError("Claude returned an empty response")
    return _strip_fence(result.stdout)


def run_openai_compatible(
    prompt: str,
    *,
    model: str,
    base_url: str,
    api_key: str | None,
    timeout: int = 1200,
) -> str:
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/v1/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise AgentError(f"OpenAI-compatible endpoint failed: {exc}") from exc
    try:
        output = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AgentError("OpenAI-compatible endpoint returned an unexpected response") from exc
    if not str(output).strip():
        raise AgentError("OpenAI-compatible endpoint returned an empty response")
    return _strip_fence(str(output))


def run_agent(
    prompt: str,
    *,
    provider: str,
    model: str | None = None,
    reasoning: str = "medium",
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: int = 1200,
) -> str:
    provider = provider.casefold()
    if provider == "codex":
        return run_codex(
            prompt,
            model=model or os.environ.get("HF_TRANSCRIPT_CODEX_MODEL", "gpt-5.6-terra"),
            reasoning=reasoning,
            timeout=timeout,
        )
    if provider == "claude":
        return run_claude(
            prompt,
            model=model or os.environ.get("HF_TRANSCRIPT_CLAUDE_MODEL", "sonnet"),
            timeout=timeout,
        )
    if provider in {"openai-compatible", "openai_compatible", "local"}:
        return run_openai_compatible(
            prompt,
            model=model or os.environ.get("HF_TRANSCRIPT_AGENT_MODEL", "qwen3:8b"),
            base_url=base_url
            or os.environ.get("HF_TRANSCRIPT_AGENT_BASE_URL", "http://127.0.0.1:11434"),
            api_key=api_key or os.environ.get("HF_TRANSCRIPT_AGENT_API_KEY"),
            timeout=timeout,
        )
    raise AgentError(f"unsupported agent provider: {provider}")
