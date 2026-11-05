"""Persistent stdio JSON-RPC client for God's Eye View's server/mcp/stdio.js.

One child process, one reader thread; requests run concurrently and are matched by id
(the globe panel issues several panel_request calls at once)."""
from __future__ import annotations

import json
import subprocess
import threading
from concurrent.futures import Future
from typing import Any

from . import config


class McpError(RuntimeError):
    pass


class StdioMcpClient:
    def __init__(self, argv: list[str] | None = None, timeout: float = 60.0):
        self._argv = argv
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._write = threading.Lock()
        self._pending: dict[int, Future] = {}
        self._next_id = 0

    def _command(self) -> list[str]:
        if self._argv:
            return self._argv
        node, home = config.node_bin(), config.checkout()
        if not node or not home:
            raise McpError("God's Eye View checkout or Node 24 not found; set GODSEYE_HOME / GODSEYE_NODE")
        return [node, str(home / "server/mcp/stdio.js"), "--api-base", config.api_base()]

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            fut = self._pending.pop(msg.get("id"), None) if "id" in msg else None
            if fut and not fut.done():
                fut.set_result(msg)
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(McpError("the MCP server exited"))
        self._pending.clear()

    def _ensure(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self._proc = subprocess.Popen(
                self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1)
            threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()
            self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "hermes-godseye", "version": "0.2.0"}})
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        with self._write:
            assert self._proc and self._proc.stdin
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        with self._write:
            self._next_id += 1
            rid = self._next_id
        fut: Future = Future()
        self._pending[rid] = fut
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            msg = fut.result(self._timeout)
        except TimeoutError:
            self._pending.pop(rid, None)
            raise McpError(f"{method} timed out") from None
        if "error" in msg:
            raise McpError(str(msg["error"].get("message", "MCP error")))
        return msg.get("result")

    def request(self, method: str, params: dict | None = None) -> Any:
        self._ensure()
        return self._rpc(method, params)

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def read_resource(self, uri: str) -> dict:
        return self.request("resources/read", {"uri": uri})

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
