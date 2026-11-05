#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${1:-$HOME/.local}"

if [[ -f "$PREFIX/pvpnlib.py" && -f "$PREFIX/pvpn" ]]; then
    SHARE_DIR="$PREFIX"
    BIN_DIR="$PREFIX"
else
    SHARE_DIR="$PREFIX/share/pvpn"
    BIN_DIR="$PREFIX/bin"
fi

mkdir -p "$SHARE_DIR" "$BIN_DIR"

cp "$SCRIPT_DIR/pvpn"          "$SHARE_DIR/pvpn"
cp "$SCRIPT_DIR/pvpnlib.py"     "$SHARE_DIR/pvpnlib.py"
cp "$SCRIPT_DIR/pvpn_servers.py" "$SHARE_DIR/pvpn_servers.py"
cp "$SCRIPT_DIR/pvpn-rotate"    "$SHARE_DIR/pvpn-rotate"
cp "$SCRIPT_DIR/README.md"      "$SHARE_DIR/README.md" 2>/dev/null || true
chmod +x "$SHARE_DIR/pvpn" "$SHARE_DIR/pvpn-rotate"

ln -sf "$SHARE_DIR/pvpn"         "$BIN_DIR/pvpn"
ln -sf "$SHARE_DIR/pvpn-rotate"  "$BIN_DIR/pvpn-rotate"

echo "Installed pvpn sources into: $SHARE_DIR"
echo "Installed commands:"
echo "  $BIN_DIR/pvpn -> $SHARE_DIR/pvpn"
echo "  $BIN_DIR/pvpn-rotate -> $SHARE_DIR/pvpn-rotate"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Note: $BIN_DIR is not on your PATH. Add it (e.g. export PATH=\"$BIN_DIR:\$PATH\")." ;;
esac

PLUGIN_SRC="$SCRIPT_DIR/plugin/vpn-rotate.js"
if [[ -f "$PLUGIN_SRC" ]]; then
    OC_PLUGIN_DIR="$HOME/.config/opencode/plugins"
    if [[ -d "$HOME/.config/opencode" ]]; then
        mkdir -p "$OC_PLUGIN_DIR"
        cp "$PLUGIN_SRC" "$OC_PLUGIN_DIR/vpn-rotate.js"
        echo "Installed opencode plugin: $OC_PLUGIN_DIR/vpn-rotate.js"
    fi
fi

missing=0
for bin in flatpak nmcli python3; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "WARNING: missing dependency: $bin" >&2
        missing=1
    fi
done
if command -v flatpak >/dev/null 2>&1; then
    if ! flatpak info com.protonvpn.www >/dev/null 2>&1; then
        echo "WARNING: ProtonVPN Flatpak com.protonvpn.www not installed." >&2
        echo "  Install it with: flatpak install flathub com.protonvpn.www" >&2
        missing=1
    fi
fi
if [[ "$missing" -ne 0 ]]; then
    echo "Installation finished with warnings; install missing pieces above." >&2
fi
echo "Done."