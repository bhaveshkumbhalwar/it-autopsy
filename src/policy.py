"""Code-enforced policy for tool execution.

The policy is the security boundary. Ticket text, retrieved documents or the
model's own output are never treated as authorization. Authorization is checked
here, in code, immediately before a tool runs.
"""

from __future__ import annotations

from src import config


# Each entry is the complete allowlist. Anything not listed here cannot run.
ALLOWED_TOOLS: dict[str, dict] = {
    "sim_get_wifi_status": {
        "risk": "read_only",
        "os": [config.OS_MACOS, config.OS_WINDOWS, config.OS_UNKNOWN],
        "simulated": True,
        "requires_approval": False,
        "params": {},
        "description": "Simulated Wi-Fi/network status.",
    },
    "sim_get_dns_config": {
        "risk": "read_only",
        "os": [config.OS_MACOS, config.OS_WINDOWS, config.OS_UNKNOWN],
        "simulated": True,
        "requires_approval": False,
        "params": {},
        "description": "Simulated DNS configuration.",
    },
    "sim_ping_host": {
        "risk": "read_only",
        "os": [config.OS_MACOS, config.OS_WINDOWS, config.OS_UNKNOWN],
        "simulated": True,
        "requires_approval": False,
        "params": {"host": "hostname"},
        "description": "Simulated reachability test.",
    },
    "sim_check_vpn": {
        "risk": "read_only",
        "os": [config.OS_MACOS, config.OS_WINDOWS, config.OS_UNKNOWN],
        "simulated": True,
        "requires_approval": False,
        "params": {},
        "description": "Simulated VPN/proxy inspection.",
    },
    "sim_renew_dhcp_lease": {
        "risk": "state_changing",
        "os": [config.OS_MACOS, config.OS_WINDOWS, config.OS_UNKNOWN],
        "simulated": True,
        "requires_approval": True,
        "params": {},
        "description": "Simulated state-changing action used to demonstrate the approval gate. Never executed for real.",
    },
    "macos_wifi_status": {
        "risk": "read_only",
        "os": [config.OS_MACOS],
        "simulated": False,
        "requires_approval": True,
        "params": {},
        "description": "Read Wi-Fi info via system_profiler (read-only, opt-in + approval).",
    },
    "macos_dns_config": {
        "risk": "read_only",
        "os": [config.OS_MACOS],
        "simulated": False,
        "requires_approval": True,
        "params": {},
        "description": "Read DNS config via scutil --dns (read-only, opt-in + approval).",
    },
    "macos_network_info": {
        "risk": "read_only",
        "os": [config.OS_MACOS],
        "simulated": False,
        "requires_approval": True,
        "params": {},
        "description": "Read network info via networksetup (read-only, opt-in + approval).",
    },
}


class PolicyViolation(Exception):
    """Raised when a tool call is not permitted. Never caught by model text."""


_OS_ALIASES = {
    "mac": config.OS_MACOS,
    "macos": config.OS_MACOS,
    "osx": config.OS_MACOS,
    "windows": config.OS_WINDOWS,
    "win": config.OS_WINDOWS,
    "unknown": config.OS_UNKNOWN,
    "": config.OS_UNKNOWN,
}


def normalize_os(value: str | None) -> str:
    return _OS_ALIASES.get((value or "").strip().lower(), config.OS_UNKNOWN)


class Policy:
    def __init__(self, os_name: str = config.OS_UNKNOWN, allow_real: bool = False):
        self.os_name = normalize_os(os_name)
        self.allow_real = bool(allow_real)

    def check(self, tool_name: str, params: dict | None = None, *, approved: bool = False) -> dict:
        if not isinstance(tool_name, str) or tool_name not in ALLOWED_TOOLS:
            raise PolicyViolation(f"unregistered tool: {tool_name!r}")
        spec = ALLOWED_TOOLS[tool_name]
        if self.os_name not in spec["os"]:
            raise PolicyViolation(
                f"tool {tool_name} is not supported on {self.os_name}"
            )
        if not spec["simulated"] and not self.allow_real:
            raise PolicyViolation(
                f"real diagnostic {tool_name} requires explicit per-run opt-in"
            )
        if spec["requires_approval"] and not approved:
            raise PolicyViolation(
                f"tool {tool_name} requires explicit user approval for this run"
            )

        allowed = spec["params"]
        supplied = dict(params or {})
        unknown = set(supplied) - set(allowed)
        if unknown:
            raise PolicyViolation(f"unexpected parameters for {tool_name}: {sorted(unknown)}")
        missing = set(allowed) - set(supplied)
        if missing:
            raise PolicyViolation(f"missing parameters for {tool_name}: {sorted(missing)}")
        for key, kind in allowed.items():
            value = supplied[key]
            if kind == "hostname":
                if not isinstance(value, str) or not _valid_hostname(value):
                    raise PolicyViolation(f"invalid hostname for {tool_name}")
            elif kind == "int":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise PolicyViolation(f"invalid integer for {tool_name}.{key}")
        return {"ok": True, "spec": spec}


def _valid_hostname(value: str) -> bool:
    import re

    if not value or len(value) > 253:
        return False
    return re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?", value) is not None
