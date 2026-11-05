"""Minimal persistent stdio JSON-RPC client for God's Eye View's server/mcp/stdio.js."""
from __future__ import annotations

import json
import subprocess
import threading
from typing import Any

from . import config


class McpError(RuntimeError):
    pass


class StdioMcpClient:
    def __init__(self, argv: list[str] | None = None, timeout: float = 45.0):
        self._argv = argv
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0

    def _command(self) -> list[str]:
        if self._argv:
            return self._argv
        node, home = config.node_bin(), config.checkout()
        if not node or not home:
            raise McpError("God's Eye View checkout or Node 24 not found; set GODSEYE_HOME / GODSEYE_NODE")
        return [node, str(home / "server/mcp/stdio.js"), "--api-base", config.api_base()]

    def _start(self) -> None:
        self._proc = subprocess.Popen(
            self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "hermes-godseye", "version": "0.1.0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        result: dict = {}

        def read() -> None:
            assert self._proc and self._proc.stdout
            for line in self._proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == rid:
                    result["msg"] = msg
                    return

        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(self._timeout)
        if "msg" not in result:
            self.close()
            raise McpError(f"{method} timed out or the MCP server exited")
        msg = result["msg"]
        if "error" in msg:
            raise McpError(str(msg["error"].get("message", "MCP error")))
        return msg.get("result")

    def request(self, method: str, params: dict | None = None) -> Any:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            return self._rpc(method, params)

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self._proc:
            self._proc.kill()
            self._proc = None


_client: StdioMcpClient | None = None


def client() -> StdioMcpClient:
    global _client
    if _client is None:
        _client = StdioMcpClient()
    return _client
