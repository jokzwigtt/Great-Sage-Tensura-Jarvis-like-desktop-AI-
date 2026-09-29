#!/usr/bin/env bash
# Great Sage setup for Linux. Run with:  bash setup.sh
set -e
cd "$(dirname "$(readlink -f "$0")")"
DIR="$(pwd)"
MODEL="gemma4:12b"

echo "=== 1/4 System packages (you may be asked for your password) ==="
if command -v apt-get >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-tk libportaudio2 espeak-ng playerctl xdotool \
    pulseaudio-utils libgtk-3-bin curl
elif command -v dnf >/dev/null; then
  sudo dnf install -y python3 python3-tkinter portaudio espeak-ng playerctl xdotool pulseaudio-utils gtk3 curl
elif command -v pacman >/dev/null; then
  sudo pacman -S --needed --noconfirm python tk portaudio espeak-ng playerctl xdotool libpulse gtk3 curl
elif command -v zypper >/dev/null; then
  sudo zypper install -y python3 python3-tk portaudio espeak-ng playerctl xdotool pulseaudio-utils gtk3-tools curl
else
  echo "Unknown package manager. Please install: python3 (with venv + tkinter), portaudio, espeak-ng,"
  echo "playerctl, xdotool, pactl (pulseaudio-utils), gtk-launch, curl - then run this again."
  exit 1
fi

echo "=== 2/4 Python packages ==="
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "=== 3/4 Ollama (local AI brain) ==="
if ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
echo "Downloading the AI model (about 8 GB, one time only)..."
ollama pull "$MODEL" || echo "Could not download the model now - run:  ollama pull $MODEL"

echo "=== 4/4 App launcher ==="
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/great-sage.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Great Sage
Comment=Your personal AI assistant
Exec="$DIR/.venv/bin/python" "$DIR/assistant.py"
Icon=$DIR/docs/icon.png
Terminal=false
Categories=Utility;
EOF
chmod +x "$APPS/great-sage.desktop"
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP" ]; then
  cp "$APPS/great-sage.desktop" "$DESKTOP/"
  chmod +x "$DESKTOP/great-sage.desktop"
  gio set "$DESKTOP/great-sage.desktop" metadata::trusted true 2>/dev/null || true
fi

echo
echo "Done! Open 'Great Sage' from your app menu or desktop."
echo "Shortcut: Ctrl+Alt+; works on X11 desktops. On Wayland, add a custom keyboard shortcut in your"
echo "system settings that runs:  \"$DIR/.venv/bin/python\" \"$DIR/assistant.py\""
echo "(opening it again just brings the running Great Sage forward)."
