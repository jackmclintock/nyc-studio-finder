#!/bin/bash
# One-time setup of the RentHop home runner on a Mac. Run from the repo folder:
#   bash local/install_mac.sh
set -e
cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
PLIST="$HOME/Library/LaunchAgents/com.nycstudiofinder.renthop.plist"
PY="$(command -v python3)"

echo "== Checking tools"
command -v python3 >/dev/null || { echo "python3 missing. Install it with: brew install python"; exit 1; }
command -v gh >/dev/null || { echo "GitHub CLI missing. Install it with: brew install gh"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Not logged in to GitHub. Run: gh auth login   (choose GitHub.com, HTTPS, login with browser)"; exit 1; }

echo "== Installing Python packages"
"$PY" -m pip install -q --user -r requirements.txt

echo "== Test run"
"$PY" -m local.renthop_local --dry-run || echo "(test run reported a problem; see above. The schedule is installed anyway.)"

echo "== Installing hourly schedule (launchd)"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.nycstudiofinder.renthop</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>-m</string><string>local.renthop_local</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/nyc-studio-finder.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/nyc-studio-finder.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load "$PLIST"
echo "== Done. It runs now and then every hour while the Mac is awake."
echo "   Log: ~/Library/Logs/nyc-studio-finder.log   Remove with: bash local/uninstall_mac.sh"
