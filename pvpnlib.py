"""
pvpnlib - control the ProtonVPN Flatpak (com.protonvpn.www) from scripts and
other programs.

This is a thin wrapper around the official ``protonvpn`` CLI that already ships
inside the ProtonVPN Flatpak.  It:
  * closes the ProtonVPN GUI when a command needs it (the official CLI refuses
    to run while the GUI is open),
  * shells out to ``flatpak run --command=protonvpn com.protonvpn.www ...``,
  * reports VPN status straight from NetworkManager (works with the GUI open),
  * returns plain dicts so you can ``import pvpnlib`` in your own project.

No third-party Python packages are required; only the system ``flatpak`` and
``nmcli`` binaries.

Import it:

    import pvpnlib
    pvpnlib.status()
    pvpnlib.connect(country="NL")
    pvpnlib.disconnect()

Or run the ``pvpn`` CLI (see ./pvpn).
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from typing import List, Optional

FLATPAK_APP = "com.protonvpn.www"
CLI_COMMAND = "protonvpn"
GUI_PROCESS_MATCH = "protonvpn-app"
VPN_INTERFACE = "proton0"
PROFILE_PREFIX = "ProtonVPN "

DEFAULT_TIMEOUT = 180


class PvpnError(RuntimeError):
    """Raised when pvpn cannot run at all (missing tools, etc.)."""


# --------------------------------------------------------------------------- #
# low level helpers
# --------------------------------------------------------------------------- #
def _which(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise PvpnError(f"'{name}' was not found on PATH")
    return path


def _run(cmd: List[str], capture: bool = True, input_text: Optional[str] = None,
         timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
    )


def _flatpak() -> str:
    return _which("flatpak")


def _nmcli() -> str:
    return _which("nmcli")


# --------------------------------------------------------------------------- #
# GUI handling
# --------------------------------------------------------------------------- #
GUI_BUS_NAME = "proton.vpn.app.gtk"


def _session_bus_has(name: str) -> Optional[bool]:
    """Return True/False if the session bus can be queried, else None."""
    busctl = shutil.which("busctl")
    if busctl:
        proc = _run([busctl, "--user", "--no-pager", "list"], capture=True, timeout=15)
        if proc.returncode == 0:
            return name in (proc.stdout or "")
    dbus_send = shutil.which("dbus-send")
    if dbus_send:
        proc = _run([dbus_send, "--session", "--dest=org.freedesktop.DBus",
                     "--type=method_call", "--print-reply", "/org/freedesktop/DBus",
                     "org.freedesktop.DBus.ListNames"], capture=True, timeout=15)
        if proc.returncode == 0:
            return name in (proc.stdout or "")
    return None


GUI_PROCESS_CMDLINE = "/app/bin/protonvpn-app"


def _gui_process_running() -> Optional[bool]:
    """Return True if the GUI process is running.

    Reads /proc and compares *argv tokens* exactly (the GUI runs as
    ``python /app/bin/protonvpn-app``), so a shell command that merely mentions
    the path is not mistaken for the GUI.  Returns None if /proc is unavailable.
    """
    if not os.path.isdir("/proc"):
        return None
    try:
        cmdlines = glob.glob("/proc/[0-9]*/cmdline")
    except OSError:
        return None
    if not cmdlines:
        return None
    for path in cmdlines:
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        for token in raw.split(b"\x00"):
            if not token:
                continue
            try:
                arg = token.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if arg == GUI_PROCESS_CMDLINE or arg.endswith("/bin/protonvpn-app"):
                return True
    return False


def gui_running() -> bool:
    """Return True if the ProtonVPN desktop (GUI) app is running.

    The GUI is the ``/app/bin/protonvpn-app`` process (the same entry point the
    .desktop launcher uses).  ``flatpak ps`` / the D-Bus name are only used as a
    fallback, because the VPN backend also runs as an instance of the Flatpak.
    """
    by_process = _gui_process_running()
    if by_process is not None:
        return by_process
    return bool(_session_bus_has(GUI_BUS_NAME))


def close_gui() -> bool:
    """Stop the ProtonVPN GUI if it is running.  Returns True if it was running."""
    if not gui_running():
        return False

    try:
        _run([_flatpak(), "kill", FLATPAK_APP], capture=True, timeout=30)
    except (PvpnError, subprocess.SubprocessError, OSError):
        pass

    if gui_running():
        pkill = shutil.which("pkill")
        if pkill:
            _run([pkill, "-f", GUI_PROCESS_MATCH], capture=True, timeout=30)

    return True


# --------------------------------------------------------------------------- #
# official CLI bridge
# --------------------------------------------------------------------------- #
def _cli_args(args: List[str]) -> List[str]:
    return [_flatpak(), "run", "--command=" + CLI_COMMAND, FLATPAK_APP] + list(args)


GUI_CONCURRENCY_ERROR = "cannot run simultaneously"


def run_cli(args: List[str], capture: bool = True, close_gui_if_needed: bool = False,
            timeout: Optional[int] = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a raw ``protonvpn`` subcommand inside the Flatpak.

    ``args`` is the argument list after the program name, e.g. ``["connect", "--country", "NL"]``.
    """
    if close_gui_if_needed:
        close_gui()
    proc = _run(_cli_args(args), capture=capture, timeout=timeout)
    if capture and proc.returncode == 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        if GUI_CONCURRENCY_ERROR in output:
            # The official CLI exits 0 but did nothing (GUI was open).
            proc.returncode = 1
    return proc


def raw(args: List[str], capture: bool = False, close_gui_if_needed: bool = True):
    """Escape hatch: pass arguments straight through to the official CLI."""
    return run_cli(args, capture=capture, close_gui_if_needed=close_gui_if_needed,
                   timeout=None if not capture else DEFAULT_TIMEOUT)


# --------------------------------------------------------------------------- #
# status (NetworkManager - works even while the GUI is open)
# --------------------------------------------------------------------------- #
def _active_proton_profile() -> Optional[str]:
    proc = _run([_nmcli(), "-t", "-f", "NAME,TYPE,DEVICE",
                 "connection", "show", "--active"], capture=True, timeout=30)
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        name, conn_type, device = parts
        if conn_type == "wireguard" and (device == VPN_INTERFACE or name.startswith(PROFILE_PREFIX)):
            return name
    return None


def status() -> dict:
    """Return the current VPN status as a dict."""
    profile = _active_proton_profile()
    if not profile:
        return {
            "connected": False,
            "server": None,
            "connection": None,
            "device": None,
            "ipv4": None,
        }

    ipv4 = None
    proc = _run([_nmcli(), "-g", "IP4.ADDRESS", "connection", "show", profile],
                capture=True, timeout=30)
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if line.strip():
                ipv4 = line.strip()
                break

    server = profile[len(PROFILE_PREFIX):] if profile.startswith(PROFILE_PREFIX) else profile
    return {
        "connected": True,
        "server": server,
        "connection": profile,
        "device": VPN_INTERFACE,
        "ipv4": ipv4,
    }


# --------------------------------------------------------------------------- #
# connect - runs inside the Flatpak sandbox using the same backend as the GUI
# --------------------------------------------------------------------------- #
# ServerFeatureEnum bits (from proton.vpn.session.servers.types)
FEATURE_SECURE_CORE = 1 << 0  # 1
FEATURE_TOR = 1 << 1          # 2
FEATURE_P2P = 1 << 2          # 4

DEFAULT_COUNTRY = "nl"

# This program is piped to `python3 -` inside the sandbox.  It talks to the
# app's own Controller (like the GUI does), so it can pick a specific country
# (e.g. the Netherlands) even on the free plan - which the official CLI blocks.
# It prints one result object between the two markers below.
_SANDBOX_CONNECT_SCRIPT = r'''
import asyncio, json, sys, os

MARK_START = "@@PVPN@@"
MARK_END = "@@ENDPVPN@@"


def emit(obj):
    sys.stdout.write(MARK_START + json.dumps(obj) + MARK_END)
    sys.stdout.flush()
    # The Proton backend can leave non-daemon threads / an event loop alive;
    # force the interpreter to exit now that the result is out.
    os._exit(0)


async def run():
    import click
    from proton.vpn.cli.core.controller import Controller, Params
    from proton.vpn.session.servers.logicals import ServerList
    from proton.vpn.session.servers.types import ServerFeatureEnum
    from proton.vpn.connection import states

    p = json.loads(sys.argv[1])
    ctx = click.Context(click.Command("pvpn"), info_name="pvpn")
    controller = await Controller.create(
        params=Params(verbose=False, allow_gui_concurrency=True), click_ctx=ctx
    )

    if not controller.is_logged_in:
        emit({"ok": False, "code": "auth",
              "error": "Not signed in. Run 'pvpn signin <user>' first."})
        return

    server_list = await controller.get_updated_server_list()

    if p.get("server"):
        target = server_list.get_by_name(p["server"])
        if target is None:
            emit({"ok": False, "code": "notfound",
                  "error": "Server '" + p["server"] + "' was not found."})
            return
    else:
        servers = server_list.logicals
        if p.get("country"):
            servers = ServerList.get_servers_in_country_code(servers, p["country"])
        if p.get("city"):
            servers = ServerList.get_servers_in_city(servers, p["city"])
        feats = int(p.get("features") or 0)
        default_excluded = ServerFeatureEnum.SECURE_CORE | ServerFeatureEnum.TOR
        excluded = (default_excluded & feats) ^ default_excluded
        servers = ServerList.get_servers_with_features(
            servers, request_features=feats, exclude_features=excluded)
        servers = ServerList.get_available_servers(servers, controller.user_tier)
        if p.get("random"):
            import random
            pool = list(servers)
            target = random.choice(pool) if pool else None
        else:
            target = ServerList.get_fastest_server(servers)

    if target is None:
        where = p.get("city") or p.get("country") or "any server"
        emit({"ok": False, "code": "nofree",
              "error": "No server available in " + str(where) + " for your plan."})
        return

    connector = await controller.get_vpn_connector()
    current = connector.current_connection
    cur_state = connector.current_state

    def describe(state, reused):
        conn = state.context.connection
        ev = getattr(state.context, "event", None)
        details = getattr(getattr(ev, "context", None), "connection_details", None)
        ip = getattr(details, "server_ipv4", None) if details else None
        city = target.city
        loc = (city + ", " + target.entry_country_name) if city else target.entry_country_name
        text = "Connected to " + conn.server_name + " in " + loc + "."
        if ip:
            text += "\nYour new IP address is " + ip + "."
        return {
            "ok": True, "connected": True, "reused": reused,
            "server": conn.server_name, "location": loc,
            "protocol": str(getattr(conn, "protocol", "") or ""),
            "ip": ip, "output": text,
        }

    if (isinstance(cur_state, states.Connected) and current is not None
            and current.server_name == target.name):
        emit(describe(cur_state, True))
        return

    try:
        new_state = await controller.connect(target)
    except Exception as exc:
        emit({"ok": False, "code": "error", "error": str(exc) or type(exc).__name__})
        return

    if new_state is None:
        emit({"ok": False, "code": "failed",
              "error": "Connection failed. Try a different server."})
        return

    emit(describe(new_state, False))


try:
    asyncio.run(run())
except Exception as exc:
    emit({"ok": False, "code": "helper",
          "error": "sandbox helper error: " + repr(exc)})
'''

_JSON_RE = re.compile(r"@@PVPN@@(.*?)@@ENDPVPN@@", re.DOTALL)


def _run_sandbox_connect(params: dict, timeout: Optional[int] = None) -> dict:
    cmd = [_flatpak(), "run", "--command=python3", FLATPAK_APP, "-", json.dumps(params)]
    try:
        proc = _run(cmd, input_text=_SANDBOX_CONNECT_SCRIPT, capture=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "timeout", "error": "Timed out talking to ProtonVPN.",
                "output": ""}

    out = proc.stdout or ""
    match = _JSON_RE.search(out)
    if not match:
        detail = (out + (proc.stderr or "")).strip()
        return {"ok": False, "code": "helper", "returncode": proc.returncode,
                "error": "Could not read a result from the VPN backend.",
                "output": detail}
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return {"ok": False, "code": "helper", "error": "Invalid backend response.",
                "output": out.strip()}
    data.setdefault("output", (data.get("error") or "").strip())
    return data


def connect(server: Optional[str] = None, country: Optional[str] = None,
            city: Optional[str] = None, p2p: bool = False, securecore: bool = False,
            tor: bool = False, random: bool = False, close_gui_if_needed: bool = True,
            default_country: Optional[str] = DEFAULT_COUNTRY,
            timeout: Optional[int] = DEFAULT_TIMEOUT) -> dict:
    """Connect to ProtonVPN.

    By default this connects to the fastest available server in the
    Netherlands (``default_country="nl"``).  Pass ``country``/``city``/``server``
    to override, or ``default_country=None`` to use Proton's global fastest.

    Returns a result dict, e.g.::

        {"ok": True, "connected": True, "server": "NL-FREE#119",
         "location": "Amsterdam, Netherlands", "protocol": "...", "ip": "...",
         "reused": False, "output": "Connected to ..."}
    """
    if close_gui_if_needed:
        close_gui()

    features = 0
    if securecore:
        features |= FEATURE_SECURE_CORE
    if tor:
        features |= FEATURE_TOR
    if p2p:
        features |= FEATURE_P2P

    selected_country = country if country else (None if server else default_country)
    params = {
        "server": server,
        "country": (selected_country or "").lower() if selected_country else None,
        "city": city,
        "features": features,
        "random": bool(random),
    }
    result = _run_sandbox_connect(params, timeout=timeout)
    if result.get("ok") and not result.get("ip"):
        # e.g. when the connection was reused, read the IP from NetworkManager.
        result["ip"] = status().get("ipv4")
    return result



def disconnect(close_gui_if_needed: bool = True,
               timeout: Optional[int] = DEFAULT_TIMEOUT) -> dict:
    """Disconnect the current VPN connection.

    NOTE: if your access to this machine goes *through* the VPN, disconnecting
    will drop that access.  The official CLI can also exit non-zero while tearing
    the tunnel down, so success is inferred from its "Disconnected." message.
    """
    proc = run_cli(["disconnect"], capture=True, close_gui_if_needed=close_gui_if_needed,
                   timeout=timeout)
    output = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0 or "Disconnected." in output
    try:
        still_connected = status()["connected"]
    except PvpnError:
        still_connected = None
    return {
        "ok": ok,
        "returncode": proc.returncode,
        "connected": still_connected,
        "output": output.strip(),
    }


# --------------------------------------------------------------------------- #
# account
# --------------------------------------------------------------------------- #
def info(close_gui_if_needed: bool = True) -> dict:
    """Return account info: {'signed_in': bool, 'account': str|None}."""
    proc = run_cli(["info"], capture=True, close_gui_if_needed=close_gui_if_needed)
    text = (proc.stdout or "") + (proc.stderr or "")
    account = None
    m = re.search(r"Account:\s*'([^']*)'", text)
    if m:
        account = m.group(1)
    signed_in = proc.returncode == 0 and account not in (None, "")
    return {"ok": proc.returncode == 0, "signed_in": signed_in,
            "account": account, "output": text.strip()}


def signin_interactive(username: str) -> int:
    """Sign in.  Runs interactively (password prompt needs a TTY).  Returns exit code."""
    proc = run_cli(["signin", username], capture=False, close_gui_if_needed=True, timeout=None)
    return proc.returncode


def signout() -> dict:
    proc = run_cli(["signout"], capture=True, close_gui_if_needed=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "output": text.strip()}


# --------------------------------------------------------------------------- #
# location discovery
# --------------------------------------------------------------------------- #
def _strip_table(text: str) -> List[str]:
    rows = []
    for line in text.splitlines():
        s = line.rstrip()
        t = s.strip()
        if not t:
            continue
        if set(t) <= set("- "):  # tabulate separator line
            continue
        rows.append(s)
    return rows


def countries() -> dict:
    """Return all countries: {'ok': bool, 'countries': [{'name','code'}], ...}."""
    proc = run_cli(["countries", "list"], capture=True, close_gui_if_needed=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    out = []
    for row in _strip_table(text):
        stripped = row.strip()
        if stripped.lower().startswith("country") and stripped.lower().endswith("code"):
            continue
        parts = stripped.rsplit(None, 1)
        if len(parts) != 2:
            continue
        name, code = parts
        out.append({"name": name, "code": code.lower()})
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "countries": out, "output": text.strip()}


_CITIES_HEADER_RE = re.compile(r"^Cities in (?P<country>.+):\s*$")


def cities(country: str) -> dict:
    """Return cities in a country: {'ok', 'country', 'cities': [{'name','features'}]}."""
    proc = run_cli(["cities", "list", country], capture=True, close_gui_if_needed=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    country_name = country
    out = []
    for line in text.splitlines():
        m = _CITIES_HEADER_RE.match(line.strip())
        if m:
            country_name = m.group("country")
            continue
    for row in _strip_table(text):
        stripped = row.strip()
        if _CITIES_HEADER_RE.match(stripped):
            continue
        if stripped.lower().startswith("city") and stripped.lower().endswith("features"):
            continue
        cols = re.split(r"\s{2,}", stripped)
        name = cols[0]
        features = []
        if len(cols) > 1 and cols[1].strip():
            features = [f.strip() for f in cols[1].split(",") if f.strip()]
        out.append({"name": name, "features": features})
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "country": country_name, "cities": out, "output": text.strip()}


def servers(close_gui_if_needed: bool = True) -> dict:
    """The official CLI only points at the web server list; mirror that."""
    proc = run_cli(["servers"], capture=True, close_gui_if_needed=close_gui_if_needed)
    text = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "output": text.strip()}


def config(args: List[str]) -> dict:
    """Pass through to ``protonvpn config ...``."""
    proc = run_cli(["config"] + list(args), capture=True, close_gui_if_needed=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "output": text.strip()}


def to_json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)
