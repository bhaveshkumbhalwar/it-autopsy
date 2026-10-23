"""Typed tool registry and safe execution.

Safety properties enforced here, not in prose:

* Every call goes through :mod:`src.policy` first (allowlist, OS support,
  parameter shape, opt-in for real diagnostics).
* No arbitrary shell command is ever constructed. Real commands are hard-coded
  argument lists with a timeout.
* Tool output is redacted (email addresses, MAC addresses, bearer tokens) before
  it is returned or logged.
* Failures return a structured, redacted error instead of a traceback.

Simulated tools are the default and are always labelled ``simulated`` in the
result. They exist so the loop can be exercised without touching the host.
"""

from __future__ import annotations

import re
import subprocess
import time

from src import config, policy

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_TOKEN_RE = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_HEX_TOKEN_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")
# Home directory names and usernames that can appear in diagnostic output.
_HOME_RE = re.compile(r"(/Users/|/home/|C:\\\\Users\\\\)([^/\s\\]+)")

MAX_OUTPUT_CHARS = 2000


def redact(text: str) -> tuple[str, bool]:
    """Return ``(redacted_text, changed)``.

    This is defence in depth for logs and UI, not a guarantee that every
    possible secret is caught. Tools are read-only and narrow by design.
    """
    if not text:
        return "", False
    original = text
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _MAC_RE.sub("[REDACTED_MAC]", text)
    text = _TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = _HEX_TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = _HOME_RE.sub(r"\1[REDACTED_USER]", text)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n[TRUNCATED]"
    return text, text != original


def redact_obj(value):
    """Recursively redact string values inside lists/dicts (for audit logs)."""
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_obj(v) for v in value]
    return value


class ToolRegistry:
    def __init__(self, os_name: str = config.OS_UNKNOWN, allow_real: bool = False):
        self.os_name = policy.normalize_os(os_name)
        self.allow_real = bool(allow_real)
        self.pol = policy.Policy(os_name=self.os_name, allow_real=self.allow_real)

    def call(self, tool_name: str, params: dict | None = None, *, approved: bool = False) -> dict:
        params = dict(params or {})
        # Raises PolicyViolation - callers must handle it explicitly.
        self.pol.check(tool_name, params, approved=approved)
        spec = policy.ALLOWED_TOOLS[tool_name]
        started = time.perf_counter()
        try:
            if spec["simulated"]:
                data = self._sim(tool_name, params)
            else:
                data = self._real(tool_name, params)
            summary, changed = redact(str(data.get("summary", "")))
            safe_data = redact_obj(data)
            changed = changed or safe_data != data
            return {
                "tool_name": tool_name,
                "simulated": bool(spec["simulated"]),
                "ok": True,
                "data": safe_data,
                "summary": summary,
                "error": "",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "redacted": changed,
            }
        except policy.PolicyViolation:
            raise
        except subprocess.TimeoutExpired:
            return self._error(tool_name, spec, "timeout", started)
        except FileNotFoundError:
            return self._error(tool_name, spec, "command_not_available", started)
        except Exception:
            # Deliberately do not echo the exception text: it may contain
            # machine-specific paths or data.
            return self._error(tool_name, spec, "tool_execution_failed", started)

    @staticmethod
    def _error(tool_name: str, spec: dict, reason: str, started: float) -> dict:
        return {
            "tool_name": tool_name,
            "simulated": bool(spec["simulated"]),
            "ok": False,
            "data": {},
            "summary": "",
            "error": reason,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "redacted": True,
        }

    # -- simulated adapters -------------------------------------------------
    # Deterministic by design. Optional ``scenario`` is deliberately *not* a
    # public tool parameter; tests inject behaviour by subclassing instead.
    def _sim(self, name: str, params: dict) -> dict:
        if name == "sim_get_wifi_status":
            return {
                "summary": "SIMULATED: Wi-Fi connected (SSID: EXAMPLE), signal good, DHCP lease valid.",
                "connected": True,
                "ssid": "EXAMPLE",
                "ip": "192.168.1.100",
                "gateway": "192.168.1.1",
            }
        if name == "sim_get_dns_config":
            return {
                "summary": "SIMULATED: DNS servers 192.168.1.1, 8.8.8.8. Search suffix example.local.",
                "dns_servers": ["192.168.1.1", "8.8.8.8"],
                "search_suffix": "example.local",
            }
        if name == "sim_ping_host":
            host = params["host"]
            return {
                "summary": f"SIMULATED: Ping to {host} succeeded (4/4), avg 20ms.",
                "host": host,
                "sent": 4,
                "received": 4,
                "avg_ms": 20,
            }
        if name == "sim_check_vpn":
            return {
                "summary": "SIMULATED: No active VPN detected.",
                "vpn_active": False,
            }
        if name == "sim_renew_dhcp_lease":
            return {
                "summary": "SIMULATED: DHCP lease renewal would run here after approval. No real change was made.",
                "changed": False,
            }
        raise ValueError("unhandled simulated tool")

    # -- real read-only adapters (opt-in, macOS only) -----------------------
    def _real(self, name: str, params: dict) -> dict:
        commands: dict[str, list[str]] = {
            "macos_wifi_status": ["system_profiler", "SPAirPortDataType"],
            "macos_dns_config": ["scutil", "--dns"],
            "macos_network_info": ["networksetup", "-getinfo", "Wi-Fi"],
        }
        argv = commands.get(name)
        if argv is None:
            raise ValueError("unhandled real tool")
        proc = subprocess.run(
            argv,  # hard-coded list; never a shell string
            capture_output=True,
            text=True,
            timeout=config.TOOL_TIMEOUT_SECONDS,
            check=False,
        )
        raw, changed = redact(proc.stdout[:4000])
        data = {
            "summary": f"Real read-only output from `{' '.join(argv)}` (exit {proc.returncode}).",
            "raw": raw,
            "returncode": proc.returncode,
        }
        if changed:
            data["summary"] += " Output was redacted."
        return data
