# Install pvpn in another VM

You are in the VM where pvpn should be installed. Run the steps below.
Use Bash. Preserve the repo URL until told to remove it.

## 1. Prerequisites

- Linux with a normal user account.
- `git`, `python3` 3.8+, `flatpak`, `nmcli`.
- The ProtonVPN Flatpak `com.protonvpn.www`.
- `~/.local/bin` on PATH (or another chosen bin dir).

Check and install OS tools:

```bash
command -v git python3 flatpak nmcli
```

If missing, install them using the distro package manager. For Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y git python3 flatpak network-manager
```

Install the ProtonVPN Flatpak (user scope is fine):

```bash
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y flathub com.protonvpn.www
flatpak info com.protonvpn.www
```

## 2. Get the (temporary) source

```bash
rm -rf "$HOME/pvpn-src"
git clone https://github.com/juro-5000/pvpn-temp-install "$HOME/pvpn-src"
cd "$HOME/pvpn-src"
```

## 3. Install

Default install into `~/.local`:

```bash
chmod +x ./install.sh
./install.sh
```

If `~/.local/bin` is not on PATH, add it. For bash:

```bash
grep -q 'HOME/.local/bin' "$HOME/.bashrc" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
export PATH="$HOME/.local/bin:$PATH"
```

Verify the commands are available and runnable:

```bash
command -v pvpn
command -v pvpn-rotate
pvpn --version
pvpn-rotate status --json
```

`pvpn-rotate status` may report not signed in until ProtonVPN credentials are configured; that is expected.

## 4. Optional opencode plugin

`install.sh` installs the plugin automatically when `~/.config/opencode` exists. Otherwise copy it manually:

```bash
mkdir -p "$HOME/.config/opencode/plugins"
cp "$HOME/pvpn-src/plugin/vpn-rotate.js" "$HOME/.config/opencode/plugins/vpn-rotate.js"
```

Set the rotation mode. Default is `ask`; use `auto` only if you want automatic VPN switching on rate limits:

```bash
pvpn-rotate mode ask
```

Restart opencode after changing plugins.

## 5. Sign in to ProtonVPN

This is interactive and must be done by the user in a terminal:

```bash
pvpn signin YOUR_USERNAME
```

Then verify:

```bash
pvpn status --json
pvpn countries list | head
```

## 6. Cleanup

The temporary repo can be removed locally after install:

```bash
rm -rf "$HOME/pvpn-src"
```

Do not delete the GitHub repo; the original owner will remove it later.

## Notes

- `pvpn connect` with no args connects to the fastest Netherlands server.
- `pvpn-rotate` defaults to `ask` mode: it logs and prints a hint but does not switch automatically.
- Disconnecting the VPN may cut access to this machine if it is reached over the VPN.