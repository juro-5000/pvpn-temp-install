"""pvpn_servers - enumerate ProtonVPN servers for the current account tier.

Reuses the Flatpak sandbox bridge from :mod:`pvpnlib` (the same backend the
ProtonVPN GUI uses) to list *logical* servers for a country, already filtered by
what the signed-in account may actually use, sorted fastest-first (lowest
``score``).

Import it::

    import pvpn_servers
    result = pvpn_servers.list_servers(country="nl")
    for s in result["servers"]:
        print(s["name"], s["load"], s["score"])

Or as a CLI::

    python3 pvpn_servers.py --country nl
    python3 pvpn_servers.py --country nl --free --limit 10 --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import pvpnlib  # noqa: E402

DEFAULT_COUNTRY = "nl"

_MARK_START = "@@PVPN@@"
_MARK_END = "@@ENDPVPN@@"
_JSON_RE = re.compile(re.escape(_MARK_START) + r"(.*?)" + re.escape(_MARK_END), re.DOTALL)

# Runs *inside* the Flatpak sandbox (piped to ``python3 -``).  Prints one JSON
# object between the markers.  Kept intentionally close to
# ``pvpnlib._SANDBOX_CONNECT_SCRIPT`` so both share the same backend login flow.
_SANDBOX_LIST_SCRIPT = r'''
import asyncio, json, os, sys


def emit(obj):
    sys.stdout.write("@@PVPN@@" + json.dumps(obj, default=str) + "@@ENDPVPN@@")
    sys.stdout.flush()
    os._exit(0)


async def run():
    import click
    from proton.vpn.cli.core.controller import Controller, Params
    from proton.vpn.session.servers.logicals import ServerList

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
    servers = list(server_list.logicals)

    if p.get("country"):
        servers = list(ServerList.get_servers_in_country_code(servers, p["country"]))
    if p.get("city"):
        servers = list(ServerList.get_servers_in_city(servers, p["city"]))
    if p.get("features") is not None:
        feats = int(p.get("features") or 0)
        servers = list(ServerList.get_servers_with_features(
            servers, request_features=feats, exclude_features=0))

    available = list(ServerList.get_available_servers(servers, controller.user_tier))

    items = []
    for s in available:
        try:
            load = float(getattr(s, "load", 0) or 0)
        except (TypeError, ValueError):
            load = 0.0
        try:
            score = float(getattr(s, "score", 0) or 0)
        except (TypeError, ValueError):
            score = 0.0
        items.append({
            "name": s.name,
            "city": str(getattr(s, "city", "") or ""),
            "country": str(getattr(s, "entry_country_name", "") or ""),
            "country_code": str(getattr(s, "entry_country", "") or ""),
            "free": bool(getattr(s, "free", False)),
            "tier": str(getattr(s, "tier", "") or ""),
            "load": load,
            "score": score,
            "under_maintenance": bool(getattr(s, "under_maintenance", False)),
        })

    emit({"ok": True, "user_tier": str(controller.user_tier),
          "count": len(items), "servers": items})


try:
    asyncio.run(run())
except Exception as exc:  # pragma: no cover - defensive; surfaced to caller
    import traceback
    emit({"ok": False, "code": "helper", "error": traceback.format_exc()})
'''


def _run_sandbox_list(params: dict, timeout: Optional[int] = None) -> dict:
    cmd = [pvpnlib._flatpak(), "run", "--command=python3", pvpnlib.FLATPAK_APP, "-",
           json.dumps(params)]
    try:
        proc = subprocess.run(cmd, input=_SANDBOX_LIST_SCRIPT, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "timeout",
                "error": "Timed out talking to ProtonVPN."}

    match = _JSON_RE.search(proc.stdout or "")
    if not match:
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return {"ok": False, "code": "helper", "returncode": proc.returncode,
                "error": "Could not read a result from the VPN backend.",
                "output": detail}
    try:
        return json.loads(match.group(1))
    except ValueError:
        return {"ok": False, "code": "helper",
                "error": "Invalid backend response.", "output": (proc.stdout or "").strip()}


def list_servers(country: str = DEFAULT_COUNTRY, city: Optional[str] = None,
                 only_free: bool = False, sort: str = "score",
                 timeout: int = 180) -> dict:
    """List servers available to the signed-in account.

    Returns a dict::

        {"ok": True, "user_tier": "0", "count": 38,
         "servers": [{"name": "NL-FREE#102", "city": "Amsterdam", "load": 41,
                      "score": 0.83, "free": True, "tier": "0", ...}, ...]}

    ``servers`` is sorted fastest-first (ascending ``score``) by default.  Pass
    ``sort="load"`` or ``sort="name"`` to change, or ``sort=None`` to keep the
    backend order.
    """
    result = _run_sandbox_list({
        "country": (country or "").lower() or None,
        "city": city,
    }, timeout=timeout)
    if not result.get("ok"):
        return result

    servers: List[dict] = result.get("servers", [])
    if only_free:
        servers = [s for s in servers if s.get("free")]
    if sort == "score":
        servers.sort(key=lambda s: (s.get("under_maintenance", False), s.get("score", 0.0), s.get("load", 0.0)))
    elif sort == "load":
        servers.sort(key=lambda s: (s.get("under_maintenance", False), s.get("load", 0.0), s.get("score", 0.0)))
    elif sort == "name":
        servers.sort(key=lambda s: s.get("name", ""))
    result["servers"] = servers
    result["count"] = len(servers)
    return result


def fastest(country: str = DEFAULT_COUNTRY, exclude: Optional[set] = None,
            only_free: bool = True, timeout: int = 180) -> Optional[dict]:
    """Return the fastest available server, skipping names in ``exclude``."""
    exclude = exclude or set()
    result = list_servers(country=country, only_free=only_free, sort="score",
                          timeout=timeout)
    if not result.get("ok"):
        return None
    for server in result["servers"]:
        if server["name"] in exclude:
            continue
        if server.get("under_maintenance"):
            continue
        return server
    return None


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="List available ProtonVPN servers.")
    parser.add_argument("--country", default=DEFAULT_COUNTRY,
                        help="country code (default: nl)")
    parser.add_argument("--city", default=None, help="city name")
    parser.add_argument("--free", action="store_true", help="only free-tier servers")
    parser.add_argument("--sort", default="score", choices=["score", "load", "name", "none"])
    parser.add_argument("--limit", type=int, default=0, help="max rows (0 = all)")
    parser.add_argument("--json", action="store_true", help="emit raw JSON")
    args = parser.parse_args(argv)

    sort = None if args.sort == "none" else args.sort
    result = list_servers(country=args.country, city=args.city,
                          only_free=args.free, sort=sort)
    if not result.get("ok"):
        sys.stderr.write("error: " + str(result.get("error", "unknown")) + "\n")
        return 1

    servers = result["servers"]
    if args.limit > 0:
        servers = servers[:args.limit]

    if args.json:
        print(json.dumps({"ok": True, "user_tier": result.get("user_tier"),
                          "count": len(servers), "servers": servers}, indent=2))
        return 0

    print(f"tier={result.get('user_tier')} count={len(servers)}")
    print(f"{'name':<14} {'city':<14} {'load':>5} {'score':>8}  free")
    for s in servers:
        print(f"{s['name']:<14} {s['city']:<14} {s['load']:>5.0f} {s['score']:>8.3f}  "
              f"{'yes' if s.get('free') else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
