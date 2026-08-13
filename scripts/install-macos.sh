#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --workspace /absolute/path [--agent none|codex|claude] [--language English]"
}

workspace=""
agent="none"
language="English"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      workspace="${2:-}"
      shift 2
      ;;
    --agent)
      agent="${2:-}"
      shift 2
      ;;
    --language)
      language="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$workspace" || "$workspace" != /* ]]; then
  echo "--workspace must be an absolute path" >&2
  exit 2
fi
if [[ "$agent" != "none" && "$agent" != "codex" && "$agent" != "claude" ]]; then
  echo "--agent must be none, codex, or claude" >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11 or newer is required" >&2
  exit 1
fi

python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 or newer is required; found $python_version" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
app_dir="$HOME/Library/Application Support/Shruta"
venv_dir="$app_dir/venv"
extension_dir="$app_dir/extension"
log_dir="$HOME/Library/Logs/Shruta"
download_dir="$HOME/Downloads/shruta-transcripts"
label="io.github.hpandey2023.shruta-sweeper"
plist="$HOME/Library/LaunchAgents/$label.plist"
legacy_label="io.github.hpandey2023.hf-transcript-sweeper"
legacy_plist="$HOME/Library/LaunchAgents/$legacy_label.plist"

mkdir -p "$app_dir" "$extension_dir" "$log_dir" "$download_dir" "$workspace/raw"
python3 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --upgrade pip
"$venv_dir/bin/python" -m pip install "$repo_dir"

cp "$repo_dir/extension/manifest.json" "$extension_dir/manifest.json"
cp "$repo_dir/extension/background.js" "$extension_dir/background.js"
cp "$repo_dir/extension/capture.js" "$extension_dir/capture.js"
cp "$repo_dir/extension/hook.js" "$extension_dir/hook.js"
mkdir -p "$extension_dir/icons"
cp "$repo_dir"/extension/icons/*.png "$extension_dir/icons/"

mkdir -p "$HOME/Library/LaunchAgents"
escaped_workspace="$(printf '%s' "$workspace" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')"
escaped_language="$(printf '%s' "$language" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')"

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$venv_dir/bin/shruta</string>
    <string>sweep</string>
    <string>--downloads</string>
    <string>$download_dir</string>
    <string>--workspace</string>
    <string>$escaped_workspace</string>
    <string>--agent</string>
    <string>$agent</string>
    <string>--language</string>
    <string>$escaped_language</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>WatchPaths</key>
  <array>
    <string>$download_dir</string>
  </array>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$log_dir/sweeper.log</string>
  <key>StandardErrorPath</key>
  <string>$log_dir/sweeper.error.log</string>
</dict>
</plist>
EOF

plutil -lint "$plist" >/dev/null
launchctl bootout "gui/$UID/$legacy_label" 2>/dev/null || true
if [[ -f "$legacy_plist" ]]; then
  mv "$legacy_plist" "$legacy_plist.disabled.$(date +%Y%m%d%H%M%S)"
fi
launchctl bootout "gui/$UID/$label" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$plist"
launchctl kickstart -k "gui/$UID/$label"

echo
echo "Installed Shruta."
echo "Workspace: $workspace"
echo "Processor: $venv_dir/bin/shruta"
echo "Agent mode: $agent"
echo
echo "Chrome: open chrome://extensions, enable Developer mode, choose Load unpacked, then select:"
echo "$extension_dir"
echo
echo "Shruta captures Teams meeting recaps opened in Chrome; it does not run inside the Teams desktop app."
