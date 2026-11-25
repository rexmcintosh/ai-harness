"""Tests for mcp-lazy, run against a fake stdio MCP server over real pipes."""
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LAZY = str(HERE / "mcp_lazy.py")
FAKE = str(HERE / "fake_mcp_server.py")
sys.path.insert(0, str(HERE))
import mcp_lazy  # noqa: E402


class Client:
    """Talks to one mcp-lazy process the way an MCP client does."""

    def __init__(self, tmp, **env):
        self.tmp = tmp
        self.log = tmp / "fake.log"
        full_env = dict(os.environ, FAKE_LOG=str(self.log))
        full_env.update(env)
        command = [sys.executable, "-I", LAZY, "--name", "fake", "--cache-dir", str(tmp / "cache"),
                   "--probe-timeout", "5", "--", sys.executable, "-I", FAKE]
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=full_env)
        self.messages = queue.Queue()
        self.stderr = []
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.messages.put(json.loads(line))
        self.messages.put(None)

    def _read_err(self):
        for line in self.proc.stderr:
            self.stderr.append(line.decode(errors="replace"))

    def send(self, message):
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        self.proc.stdin.flush()

    def recv(self, timeout=10):
        message = self.messages.get(timeout=timeout)
        assert message is not None, "mcp-lazy closed its output:\n" + "".join(self.stderr)
        return message

    def request(self, method, params=None, timeout=10):
        self.next_id += 1
        rid = self.next_id
        message = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        others = []
        while True:
            reply = self.recv(timeout)
            if reply.get("id") == rid and "method" not in reply:
                return reply, others
            others.append(reply)

    def initialize(self, client="test-client", protocol="2025-06-18"):
        reply, _ = self.request("initialize", {
            "protocolVersion": protocol, "capabilities": {"roots": {}},
            "clientInfo": {"name": client, "version": "9"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return reply

    def events(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def starts(self):
        return [e for e in self.events() if e["event"] == "start"]

    def close(self, timeout=15):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        return self.proc.wait(timeout=timeout)

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


class Tmp:
    """A test folder that also starts clients in it."""

    def __init__(self, path):
        self.path = path
        self.clients = []

    def __truediv__(self, other):
        return self.path / other

    def client(self, **env):
        client = Client(self.path, **env)
        self.clients.append(client)
        return client


@pytest.fixture
def tmp(tmp_path):
    folder = Tmp(tmp_path)
    yield folder
    for client in folder.clients:
        client.kill()
    # No fake server or grandchild may outlive a test.
    log = tmp_path / "fake.log"
    if log.exists():
        pids = []
        for line in log.read_text().splitlines():
            entry = json.loads(line)
            pids.append(entry["pid"])
            if entry["event"] == "grandchild":
                pids.append(entry["child"])
        assert mcp_lazy.wait_gone(set(pids), 10), "a server process was left running"


def call(client, name, **arguments):
    return client.request("tools/call", {"name": name, "arguments": arguments})


def prime(tmp, **env):
    """Run one session so the cache exists, then end it."""
    client = tmp.client(**env)
    client.initialize()
    assert client.close() == 0
    return client


def test_cache_miss_probes_once_then_stays_lazy(tmp):
    client = tmp.client()
    reply = client.initialize()
    assert reply["result"]["serverInfo"] == {"name": "fake", "version": "1"}
    tools, _ = client.request("tools/list")
    assert [t["name"] for t in tools["result"]["tools"]] == ["echo", "ask_roots", "big", "crash"]
    assert client.request("ping")[0]["result"] == {}
    # The probe server started once and has already stopped.
    assert mcp_lazy.wait_gone({e["pid"] for e in client.starts()}, 5)
    assert len(client.starts()) == 1
    # The probe replayed the client's own initialize parameters.
    init = [e for e in client.events() if e["event"] == "recv" and e["method"] == "initialize"]
    assert init[0]["params"]["clientInfo"] == {"name": "test-client", "version": "9"}
    assert list((tmp / "cache").glob("fake-*.json"))
    assert client.close() == 0


def test_cache_hit_starts_nothing(tmp):
    prime(tmp)
    before = len(prime(tmp).starts())
    client = tmp.client()
    assert client.initialize()["result"]["serverInfo"]["name"] == "fake"
    assert len(client.request("tools/list")[0]["result"]["tools"]) == 4
    assert client.request("ping")[0]["result"] == {}
    time.sleep(0.3)
    assert len(client.starts()) == before == 1
    assert client.close() == 0


def test_first_tool_call_starts_server_and_passes_through(tmp):
    prime(tmp)
    client = tmp.client()
    client.initialize()
    reply, others = call(client, "echo", text="hello")
    assert reply["result"]["content"][0]["text"] == "hello"
    # A server notification reached the client.
    assert any(m.get("method") == "notifications/message" for m in others)
    assert len(client.starts()) == 2
    reply, _ = call(client, "echo", text="again")
    assert reply["result"]["content"][0]["text"] == "again"
    assert len(client.starts()) == 2
    live = [e for e in client.events() if e["pid"] == client.starts()[1]["pid"]]
    methods = [e["method"] for e in live if e["event"] == "recv"]
    # The live server got the replayed handshake exactly once.
    assert methods.count("initialize") == 1
    assert methods.count("notifications/initialized") == 1
    assert methods[:2] == ["initialize", "notifications/initialized"]
    assert client.close() == 0


def test_server_to_client_request_round_trip(tmp):
    prime(tmp)
    client = tmp.client()
    client.initialize()
    client.next_id += 1
    rid = client.next_id
    client.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                 "params": {"name": "ask_roots", "arguments": {}}})
    while True:
        message = client.recv()
        if message.get("method") == "roots/list":
            break
    client.send({"jsonrpc": "2.0", "id": message["id"], "result": {"roots": [{"uri": "file:///w"}]}})
    while True:
        message = client.recv()
        if message.get("id") == rid:
            break
    assert json.loads(message["result"]["content"][0]["text"]) == {"roots": [{"uri": "file:///w"}]}
    assert client.close() == 0


def test_large_message_passes_intact(tmp):
    prime(tmp)
    client = tmp.client()
    client.initialize()
    reply, _ = call(client, "big", size=3_000_000)
    assert reply["result"]["content"][0]["text"] == "x" * 3_000_000
    assert client.close() == 0


def test_paged_tool_list_is_cached_whole(tmp):
    client = tmp.client(FAKE_PAGE_SIZE="1")
    client.initialize()
    tools, _ = client.request("tools/list")
    assert [t["name"] for t in tools["result"]["tools"]] == ["echo", "ask_roots", "big", "crash"]
    assert "nextCursor" not in tools["result"]
    assert client.close() == 0


def test_changed_tool_list_updates_cache_and_notifies(tmp):
    prime(tmp, FAKE_TOOLS="echo")
    client = tmp.client(FAKE_TOOLS="echo,newtool")
    client.initialize()
    assert [t["name"] for t in client.request("tools/list")[0]["result"]["tools"]] == ["echo"]
    reply, others = call(client, "echo", text="x")
    deadline = time.monotonic() + 5
    while not any(m.get("method") == "notifications/tools/list_changed" for m in others):
        others.append(client.recv(max(0.1, deadline - time.monotonic())))
    tools, _ = client.request("tools/list")
    assert [t["name"] for t in tools["result"]["tools"]] == ["echo", "newtool"]
    assert client.close() == 0
    # The next session is served the new list from the cache.
    fresh = tmp.client(FAKE_TOOLS="echo,newtool")
    fresh.initialize()
    assert [t["name"] for t in fresh.request("tools/list")[0]["result"]["tools"]] == ["echo", "newtool"]
    assert fresh.close() == 0


def test_unadvertised_method_is_refused_without_starting(tmp):
    prime(tmp)
    client = tmp.client()
    client.initialize()
    reply, _ = client.request("resources/list")
    assert reply["error"]["code"] == -32601
    assert len(client.starts()) == 1
    assert client.close() == 0


def test_failed_start_returns_error_and_stays_alive(tmp):
    prime(tmp)
    client = tmp.client(FAKE_FAIL="refuse")
    client.initialize()
    reply, _ = call(client, "echo", text="x")
    assert reply["error"]["code"] == -32603
    assert "could not start" in reply["error"]["message"]
    assert client.request("ping")[0]["result"] == {}
    assert client.close() == 0


def test_probe_failure_falls_back_to_pass_through(tmp):
    client = tmp.client(FAKE_FAIL="exit")
    client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "c", "version": "1"}}})
    # Pass-through to a server that exits: the client sees the server end,
    # as it would without mcp-lazy.
    assert client.proc.wait(timeout=15) != 0
    assert client.messages.get(timeout=5) is None


def test_corrupt_cache_is_treated_as_a_miss(tmp):
    prime(tmp)
    for path in (tmp / "cache").glob("fake-*.json"):
        path.write_text("{not json")
    client = tmp.client()
    assert client.initialize()["result"]["serverInfo"]["name"] == "fake"
    assert len(client.starts()) == 2
    assert client.close() == 0


def test_cache_key_depends_on_command_and_all_initialize_params():
    base = {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code", "version": "1"}}
    key = mcp_lazy.cache_key(["npx", "a@1"], base)
    assert key == mcp_lazy.cache_key(["npx", "a@1"], dict(base))
    assert key != mcp_lazy.cache_key(["npx", "a@1"], dict(base, clientInfo={"name": "claude-code", "version": "2"}))
    assert key != mcp_lazy.cache_key(["npx", "a@1"], dict(base, capabilities={"roots": {}}))
    assert key != mcp_lazy.cache_key(["npx", "a@2"], base)
    assert key != mcp_lazy.cache_key(["npx", "a@1"], dict(base, protocolVersion="2025-11-25"))
    assert key != mcp_lazy.cache_key(["npx", "a@1"], dict(base, clientInfo={"name": "codex", "version": "1"}))


def live_client(tmp, **env):
    prime(tmp, **{k: v for k, v in env.items() if k == "FAKE_TOOLS"})
    client = tmp.client(**env)
    client.initialize()
    assert call(client, "echo", text="x")[0]["result"]
    pid = client.starts()[-1]["pid"]
    return client, pid


def test_end_of_input_stops_the_server(tmp):
    client, pid = live_client(tmp)
    assert client.close() == 0
    assert mcp_lazy.wait_gone({pid}, 5)
    assert [e for e in client.events() if e["pid"] == pid][-1]["event"] == "exit"


def test_sigterm_stops_a_stubborn_server_tree(tmp):
    client, pid = live_client(tmp, FAKE_IGNORE_EOF="1", FAKE_GRANDCHILD="1")
    grandchild = [e for e in client.events() if e["event"] == "grandchild"][-1]["child"]
    client.proc.send_signal(signal.SIGTERM)
    assert client.proc.wait(timeout=15) == 0
    assert mcp_lazy.wait_gone({pid, grandchild}, 5)


def test_sigkill_of_the_stand_in_still_ends_the_server(tmp):
    client, pid = live_client(tmp)
    client.proc.kill()
    client.proc.wait()
    # The server sees end of input and exits by itself.
    assert mcp_lazy.wait_gone({pid}, 5)


def test_server_crash_ends_the_stand_in(tmp):
    client, pid = live_client(tmp)
    client.next_id += 1
    client.send({"jsonrpc": "2.0", "id": client.next_id, "method": "tools/call",
                 "params": {"name": "crash", "arguments": {}}})
    assert client.proc.wait(timeout=10) == 7
    assert mcp_lazy.wait_gone({pid}, 5)


def test_client_ids_that_look_internal_are_passed_through(tmp):
    prime(tmp)
    client = tmp.client()
    client.initialize()
    call(client, "echo", text="start")
    for rid in ("mcp-lazy-tools-1", "mcp-lazy-init", mcp_lazy.TOOLS_ID + "1"):
        client.send({"jsonrpc": "2.0", "id": rid, "method": "tools/list"})
        while True:
            message = client.recv()
            if message.get("id") == rid:
                break
        assert len(message["result"]["tools"]) == 4
    assert client.close() == 0


def test_server_that_stops_reading_ends_the_session(tmp):
    client, pid = live_client(tmp)
    call(client, "close_stdin")
    deadline = time.monotonic() + 5
    while not any(e["event"] == "stdin-closed" for e in client.events()):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    client.next_id += 1
    client.send({"jsonrpc": "2.0", "id": client.next_id, "method": "tools/call",
                 "params": {"name": "echo", "arguments": {"text": "x"}}})
    # The stand-in cannot deliver the request, so it ends instead of hanging.
    assert client.proc.wait(timeout=20) == 1
    assert mcp_lazy.wait_gone({pid}, 5)


def test_sessions_that_start_together_share_one_probe(tmp):
    clients = [tmp.client() for _ in range(4)]
    for client in clients:
        client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "test-client", "version": "9"}}})
    for client in clients:
        assert client.recv()["result"]["serverInfo"]["name"] == "fake"
    assert len(clients[0].starts()) == 1
    for client in clients:
        assert client.close() == 0


def test_repeated_signals_still_shut_down_cleanly(tmp):
    client, pid = live_client(tmp, FAKE_IGNORE_EOF="1")
    for _ in range(3):
        client.proc.send_signal(signal.SIGTERM)
        time.sleep(0.05)
    assert client.proc.wait(timeout=15) == 0
    assert mcp_lazy.wait_gone({pid}, 5)


def test_usage_errors():
    with pytest.raises(SystemExit):
        mcp_lazy.main(["--name", "x"])
    with pytest.raises(SystemExit):
        mcp_lazy.main(["--name", "../bad", "--", "true"])
