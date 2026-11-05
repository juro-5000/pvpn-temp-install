# pvpn - control ProtonVPN from the command line

`pvpn` is a small, dependency-free CLI (plus an importable library) that lets
you - or any other program on this machine - control the **ProtonVPN Flatpak**
(`com.protonvpn.www`).

It was built for a Raspberry Pi running the ProtonVPN Flatpak, where the goal is
to let other programs switch the VPN **without** clicking around the GUI.

By default it connects to the **fastest server in the Netherlands**. It can also
list countries/cities, show status, sign in/out, and disconnect.

## How it works

There is already an official `protonvpn` CLI inside the Flatpak, but:

* it **refuses to run while the GUI is open**, and
* on the **free plan** it **refuses to pick a country/city/server** at all.

So `pvpn` does two things:

1. **Status** is read straight from **NetworkManager** (`nmcli`), so it works
   even while the GUI is open.
2. **Connecting** runs a tiny Python program *inside the Flatpak sandbox* that
   imports the app's own backend (`proton.vpn.cli.core.controller.Controller`)
   - the exact code the GUI uses - and asks it for the fastest server in your
   chosen country. This is why `pvpn connect` can connect to the Netherlands on
   a free account, while `protonvpn connect --country NL` cannot.
3. Account/sign-in and country/city listing delegate to the official CLI.

The GUI is closed automatically before a command that needs it
(`--keep-gui` to refuse instead).

## Requirements

* Linux with the ProtonVPN **Flatpak** installed (`com.protonvpn.www`).
* `flatpak` and `nmcli` on `PATH`.
* Python 3.8+ on the host (no pip packages needed).

## Install

```bash
./install.sh                 # symlinks ./pvpn into ~/.local/bin
# or: ./install.sh /usr/local/bin
```

Then:

```bash
pvpn status
pvpn connect
```

You can also run it in place: `./pvpn status`.

## Usage

```
pvpn status [--json]
pvpn connect [SERVER] [--country NL] [--city Amsterdam]
             [--p2p] [--securecore] [--tor] [--random] [--any-country]
             [--json] [--keep-gui]
pvpn disconnect [--json] [--keep-gui]
pvpn info [--json]
pvpn signin <username>
pvpn signout [--json]
pvpn servers [--json]
pvpn countries list [--json]
pvpn cities <country> [--json]
pvpn config <args...> [--json]
pvpn raw <args...>            # escape hatch straight to the official CLI
```

Examples:

```bash
pvpn status                       # human-readable status
pvpn connect                      # fastest NL server
pvpn connect --city Amsterdam     # fastest NL server in Amsterdam
pvpn connect --country US         # fastest US server (if your plan allows)
pvpn connect --any-country        # ignore the NL default
pvpn connect --json               # machine-readable result
```

### JSON output (for other programs)

Every command supports `--json`. Examples:

```jsonc
// pvpn status --json
{"connected": true, "server": "NL-FREE#281",
 "connection": "ProtonVPN NL-FREE#281", "device": "proton0",
 "ipv4": "10.2.0.2/32"}

// pvpn connect --json
{"ok": true, "connected": true, "reused": false,
 "server": "NL-FREE#119", "location": "Amsterdam, Netherlands",
 "protocol": "wireguard", "ip": "149.34.244.167",
 "output": "Connected to NL-FREE#119 in Amsterdam, Netherlands."}

// pvpn countries list --json
{"ok": true, "countries": [{"name": "Netherlands", "code": "nl"}, ...]}

// pvpn cities NL --json
{"ok": true, "country": "Netherlands",
 "cities": [{"name": "Amsterdam", "features": ["P2P"]}]}
```

`connect` is idempotent: if you are already on the chosen server it reports
`"reused": true` instead of reconnecting.

## Using it from your own program

Instead of shelling out, import the library:

```python
import sys
sys.path.insert(0, "/home/juro/pvpn")
import pvpnlib

pvpnlib.status()                       # -> dict
pvpnlib.connect(city="Amsterdam")      # -> dict
pvpnlib.connect(country="US")          # -> dict
pvpnlib.disconnect()
pvpnlib.countries()
pvpnlib.cities("NL")
```

`pvpnlib.py` is self-contained (standard library only); copy it next to your
project's code and it works the same.

## Caveats

* **Disconnecting may cut your access to this machine** if you reach it through
  the VPN. The CLI prints a warning; other callers should expect the same.
* Switching/connecting can briefly interrupt the tunnel while the old
  connection is replaced.
* The in-sandbox helper imports ProtonVPN's internal Python modules, so a major
  Flatpak update could require small adjustments (the module paths are the same
  ones the CLI uses).
* Only the Flatpak edition is supported (`com.protonvpn.www`).
