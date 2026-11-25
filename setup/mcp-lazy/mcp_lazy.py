#!/usr/bin/python3 -I
"""mcp-lazy: start a stdio MCP server only when a client first needs it.

Usage:
    mcp-lazy --name NAME [--cache-dir DIR] [--probe-timeout SECONDS] -- COMMAND [ARG ...]

Claude Code and Codex start every configured stdio MCP server when a session
starts, and keep it running for the whole session. For a server that most
sessions never use (a browser helper, for example) that costs memory in every
session. mcp-lazy stands in for the server:

* It answers `initialize` and `tools/list` from a cache that was recorded from
  the real server for the same command line, the same protocol version and the
  same client name.
* On a cache miss it starts the real server once, records its answers, stops
  it again, and then stays lazy.
* On the first request it cannot answer itself (normally the first
  `tools/call`), it starts the real server, replays the client's own
  `initialize` parameters, and from then on passes every message through
  unchanged in both directions.
* After a real start it asks the server for its tools again. If the list has
  changed, it updates the cache and sends `notifications/tools/list_changed`
  so the client fetches the new list.

If the cache cannot be built, mcp-lazy falls back to plain pass-through, which
is what the client would have got without it.
"""
import argparse
import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time

CACHE_VERSION = 1
INIT_ID = "mcp-lazy-init"
TOOLS_ID = "mcp-lazy-tools-"
CLOSE_GRACE = 5.0
TERM_GRACE = 3.0
# Request families that a server only answers when it advertises the matching
# capability. Without the capability, the real server would reply "method not
# found" too, so mcp-lazy replies that itself instead of starting the server.
CAPABILITY_OF = {
    "resources/": "resources",
    "prompts/": "prompts",
    "completion/": "completions",
    "logging/": "logging",
}
NAME = "mcp-lazy"
LOG_FILE = None


def log(message):
    try:
        sys.stderr.write(f"mcp-lazy[{NAME}]: {message}\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    if LOG_FILE:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            with open(LOG_FILE, "a") as handle:
                handle.write(f"{stamp} pid={os.getpid()} {NAME}: {message}\n")
        except OSError:
            pass


def debug(message):
    if os.environ.get("MCP_LAZY_DEBUG"):
        log(message)


class StartError(Exception):
    """The real server did not start or did not finish its handshake."""


# ---------------------------------------------------------------------------
# Cache


def cache_key(command, init_params):
    params = init_params if isinstance(init_params, dict) else {}
    client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
    return {
        "v": CACHE_VERSION,
        "command": list(command),
        "protocolVersion": params.get("protocolVersion"),
        "client": client.get("name"),
    }


def cache_path(cache_dir, name, key):
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:20]
    return os.path.join(cache_dir, f"{name}-{digest}.json")


def load_cache(path, key):
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("key") != key:
        return None
    init = data.get("initialize")
    tools = data.get("tools")
    if not isinstance(init, dict) or not isinstance(init.get("capabilities"), dict):
        return None
    if "protocolVersion" not in init or not isinstance(tools, list):
        return None
    return data


def save_cache(path, key, init_result, tools):
    data = {"key": key, "initialize": init_result, "tools": tools,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(data).encode())
        os.replace(tmp, path)
    except OSError as error:
        log(f"could not save cache {path}: {error}")


# ---------------------------------------------------------------------------
# Process tree helpers


def descendants(pid):
    """Return the pids of every process below pid (Linux /proc walk)."""
    children = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                stat = handle.read().decode(errors="replace")
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(entry))
    found, todo = [], [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            found.append(child)
            todo.append(child)
    return found


def alive(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            stat = handle.read().decode(errors="replace")
        return stat[stat.rindex(")") + 2] != "Z"
    except (OSError, ValueError, IndexError):
        return False


def signal_all(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def wait_gone(pids, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(alive(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return not any(alive(pid) for pid in pids)


# ---------------------------------------------------------------------------
# The real server


class Child:
    """One run of the real server, with a thread that reads its stdout."""

    def __init__(self, command):
        try:
            self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except OSError as error:
            raise StartError(f"cannot run {command[0]}: {error}") from error
        self.lines = queue.Queue()
        self.forward = None
        self.mode_lock = threading.Lock()
        # Re-entrant: a signal handler on the main thread may call close()
        # while the main thread is inside send().
        self.write_lock = threading.RLock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        stream = self.proc.stdout
        while True:
            try:
                line = stream.readline()
            except (OSError, ValueError):
                line = b""
            line = line or None
            with self.mode_lock:
                forward = self.forward
                if forward is None:
                    self.lines.put(line)
            if forward is not None:
                forward(line)
            if line is None:
                return

    def send(self, data):
        if isinstance(data, dict):
            data = json.dumps(data, separators=(",", ":")).encode() + b"\n"
        elif not data.endswith(b"\n"):
            data += b"\n"
        with self.write_lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError) as error:
                raise StartError(f"server input closed: {error}") from error

    def recv(self, deadline, held):
        """Return the next parsed message; queue anything unparseable in held."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StartError("server did not answer in time")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                raise StartError("server did not answer in time") from None
            if line is None:
                raise StartError(f"server exited (code {self.proc.poll()})")
            try:
                message = json.loads(line)
            except ValueError:
                held.append(line)
                continue
            return line, message

    def go_live(self, forward, held):
        """Forward held lines, then every later line, in order."""
        with self.mode_lock:
            pending = list(held)
            while True:
                try:
                    pending.append(self.lines.get_nowait())
                except queue.Empty:
                    break
            for line in pending:
                forward(line)
            self.forward = forward

    def close(self, grace=CLOSE_GRACE):
        """Close the server's input, then stop the whole process tree.

        End of input is the normal way to stop an MCP server, and it lets a
        browser helper close its browser. Only processes that are still
        running after the grace period get SIGTERM, then SIGKILL.
        """
        proc = self.proc
        # Take the tree first: once the top process exits, its orphans are
        # re-parented and can no longer be found from its pid.
        tree = set(descendants(proc.pid))
        with self.write_lock:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        if proc.poll() is None:
            tree.add(proc.pid)
            tree.update(descendants(proc.pid))
        tree = [pid for pid in tree if alive(pid)]
        if tree:
            log(f"server did not stop on end of input; sending SIGTERM to {len(tree)} process(es)")
            signal_all(tree, signal.SIGTERM)
            if not wait_gone(tree, TERM_GRACE):
                signal_all([pid for pid in tree if alive(pid)], signal.SIGKILL)
                wait_gone(tree, 1.0)
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass


def handshake(child, init_params, timeout):
    """Run initialize + initialized on a fresh child. Return (result, held)."""
    held = []
    deadline = time.monotonic() + timeout
    child.send({"jsonrpc": "2.0", "id": INIT_ID, "method": "initialize", "params": init_params})
    while True:
        line, message = child.recv(deadline, held)
        if isinstance(message, dict) and message.get("id") == INIT_ID and "method" not in message:
            break
        held.append(line)
    if "result" not in message or not isinstance(message["result"], dict):
        raise StartError(f"server refused initialize: {json.dumps(message.get('error'))[:300]}")
    child.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return message["result"], held


def probe(command, init_params, timeout):
    """Start the server, record its initialize result and tools, stop it."""
    child = Child(command)
    try:
        deadline = time.monotonic() + timeout
        init_result, held = handshake(child, init_params, timeout)
        tools, cursor, page = [], None, 0
        while True:
            page += 1
            request = {"jsonrpc": "2.0", "id": f"{TOOLS_ID}{page}", "method": "tools/list"}
            if cursor:
                request["params"] = {"cursor": cursor}
            child.send(request)
            while True:
                _, message = child.recv(deadline, held)
                if not isinstance(message, dict):
                    continue
                if message.get("id") == request["id"] and "method" not in message:
                    break
                if "method" in message and "id" in message:
                    child.send(probe_answer(message))
            result = message.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise StartError(f"server refused tools/list: {json.dumps(message.get('error'))[:300]}")
            tools.extend(result["tools"])
            cursor = result.get("nextCursor")
            if not cursor:
                return init_result, tools
    finally:
        child.close()


def probe_answer(request):
    """Answer a server-to-client request during a probe run."""
    method = request.get("method")
    if method == "roots/list":
        return {"jsonrpc": "2.0", "id": request["id"], "result": {"roots": []}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request["id"], "result": {}}
    return {"jsonrpc": "2.0", "id": request["id"],
            "error": {"code": -32601, "message": "mcp-lazy probe: not supported"}}


# ---------------------------------------------------------------------------
# The stand-in


class Proxy:
    def __init__(self, name, command, cache_dir, probe_timeout, stdin, stdout):
        self.name = name
        self.command = command
        self.cache_dir = cache_dir
        self.probe_timeout = probe_timeout
        self.stdin = stdin
        self.stdout = stdout
        self.out_lock = threading.Lock()
        self.state = "new"          # new -> lazy -> live
        self.child = None
        self.init_params = None
        self.key = None
        self.cache_file = None
        self.init_result = None
        self.served_tools = None
        self.swallow_initialized = True
        self.refresh_tools = []
        self.live_init = None
        self.finish_lock = threading.Lock()
        self.finisher = None

    # -- output to the client ---------------------------------------------

    def write(self, data):
        if isinstance(data, dict):
            data = json.dumps(data, separators=(",", ":")).encode() + b"\n"
        elif not data.endswith(b"\n"):
            data += b"\n"
        with self.out_lock:
            try:
                self.stdout.write(data)
                self.stdout.flush()
            except (OSError, ValueError):
                self.finish(0, "client output closed")

    def reply(self, request, result):
        self.write({"jsonrpc": "2.0", "id": request["id"], "result": result})

    def reply_error(self, request, code, message):
        self.write({"jsonrpc": "2.0", "id": request["id"], "error": {"code": code, "message": message}})

    # -- lifecycle --------------------------------------------------------

    def finish(self, code, reason):
        """Stop the real server and leave the process."""
        with self.finish_lock:
            finisher = self.finisher
            if finisher is None:
                self.finisher = threading.get_ident()
        if finisher is not None:
            if finisher == threading.get_ident():
                return  # a signal arrived while this thread was already finishing
            while True:  # another thread is finishing and will end the process
                time.sleep(1)
        log(f"exiting: {reason}")
        child, self.child = self.child, None
        if child is not None:
            child.close()
        try:
            self.stdout.flush()
        except (OSError, ValueError):
            pass
        os._exit(code)

    def run(self):
        while True:
            line = self.stdin.readline()
            if not line:
                self.finish(0, "client closed input")
            try:
                self.handle(line)
            except Exception as error:  # fail open rather than lose the client
                log(f"internal error: {error!r}; switching to pass-through")
                self.fail_open(line)

    def handle(self, line):
        if self.state == "live":
            if self.swallow_initialized and b"notifications/initialized" in line:
                message = parse(line)
                if isinstance(message, dict) and message.get("method") == "notifications/initialized":
                    self.swallow_initialized = False
                    return
            self.send_child(line)
            return
        message = parse(line)
        if not isinstance(message, dict):
            if self.state == "lazy":
                self.start_live(line, None)
            else:
                log("ignoring unreadable message before initialize")
            return
        method = message.get("method")
        is_request = method is not None and "id" in message
        if self.state == "new":
            if method == "initialize" and is_request:
                self.initialize(message, line)
            elif method == "ping" and is_request:
                self.reply(message, {})
            elif is_request:
                self.reply_error(message, -32002, "server not initialized")
            return
        # lazy
        if method is None:
            debug("dropping client response while lazy")
        elif not is_request:
            debug(f"dropping client notification {method} while lazy")
        elif method == "ping":
            self.reply(message, {})
        elif method == "tools/list":
            self.reply(message, {"tools": self.served_tools})
        elif self.unadvertised(method):
            self.reply_error(message, -32601, f"Method not found: {method}")
        else:
            self.start_live(line, message)

    def unadvertised(self, method):
        capabilities = self.init_result.get("capabilities") or {}
        for prefix, capability in CAPABILITY_OF.items():
            if method.startswith(prefix):
                return capability not in capabilities
        return False

    # -- initialize -------------------------------------------------------

    def initialize(self, message, line):
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        self.init_params = params
        self.key = cache_key(self.command, params)
        self.cache_file = cache_path(self.cache_dir, self.name, self.key)
        cached = load_cache(self.cache_file, self.key)
        label = f"client {client.get('name')} {client.get('version')} protocol {params.get('protocolVersion')}"
        if cached is None:
            log(f"{label}: no cache, asking the real server once")
            started = time.monotonic()
            try:
                init_result, tools = probe(self.command, params, self.probe_timeout)
            except StartError as error:
                log(f"probe failed ({error}); switching to pass-through")
                self.fail_open(line)
                return
            save_cache(self.cache_file, self.key, init_result, tools)
            log(f"probe took {time.monotonic() - started:.1f}s; {len(tools)} tools cached; staying lazy")
            cached = {"initialize": init_result, "tools": tools}
        else:
            log(f"{label}: cache hit, {len(cached['tools'])} tools; staying lazy")
        self.init_result = cached["initialize"]
        self.served_tools = cached["tools"]
        self.state = "lazy"
        self.reply(message, self.init_result)

    # -- going live -------------------------------------------------------

    def start_live(self, line, message):
        method = message.get("method") if isinstance(message, dict) else "batch"
        log(f"first {method}: starting the real server")
        started = time.monotonic()
        child = None
        try:
            child = Child(self.command)
            self.live_init, held = handshake(child, self.init_params, self.probe_timeout)
        except StartError as error:
            if child is not None:
                child.close(grace=1.0)
            log(f"could not start the real server: {error}")
            if isinstance(message, dict) and "id" in message:
                self.reply_error(message, -32603, f"mcp-lazy could not start {self.name}: {error}")
            return
        self.child = child
        self.state = "live"
        self.swallow_initialized = False
        child.go_live(self.from_child, held)
        log(f"real server ready after {time.monotonic() - started:.1f}s")
        self.request_tools(1, None)
        self.send_child(line)

    def fail_open(self, line):
        """Pass the client straight through to the real server."""
        if self.state == "live":
            self.send_child(line)
            return
        if self.state == "lazy":
            # The client is already initialized, so the real server needs the
            # replayed handshake before it sees this message.
            self.start_live(line, parse(line))
            return
        try:
            child = Child(self.command)
        except StartError as error:
            self.finish(1, str(error))
        self.child = child
        self.state = "live"
        self.swallow_initialized = False
        child.go_live(self.from_child, [])
        self.send_child(line)

    def send_child(self, line):
        try:
            self.child.send(line)
        except StartError as error:
            log(f"cannot write to the real server: {error}")

    def request_tools(self, page, cursor):
        request = {"jsonrpc": "2.0", "id": f"{TOOLS_ID}{page}", "method": "tools/list"}
        if cursor:
            request["params"] = {"cursor": cursor}
        if page == 1:
            self.refresh_tools = []
        try:
            self.child.send(request)
        except (StartError, AttributeError):
            pass

    def from_child(self, line):
        """Called on the reader thread for every line the real server writes."""
        if line is None:
            child = self.child
            code = child.proc.wait() if child is not None else 1
            self.finish(code if code >= 0 else 128 - code, f"real server exited (code {code})")
            return
        if b'"' + TOOLS_ID.encode() in line:
            message = parse(line)
            if (isinstance(message, dict) and "method" not in message
                    and isinstance(message.get("id"), str) and message["id"].startswith(TOOLS_ID)):
                self.tools_page(message)
                return
        self.write(line)

    def tools_page(self, message):
        result = message.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            log("tools refresh failed; keeping the cache")
            return
        self.refresh_tools.extend(result["tools"])
        cursor = result.get("nextCursor")
        if cursor:
            page = int(message["id"][len(TOOLS_ID):]) + 1
            self.request_tools(page, cursor)
            return
        tools = self.refresh_tools
        init_result = self.live_init or self.init_result
        if init_result is not None and self.cache_file is not None:
            save_cache(self.cache_file, self.key, init_result, tools)
        if self.served_tools is not None and tools != self.served_tools:
            log("tool list changed since the cache was made; telling the client")
            self.served_tools = tools
            self.write({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})


def parse(line):
    try:
        return json.loads(line)
    except ValueError:
        return None


def default_cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcp-lazy")


def main(argv=None):
    global NAME, LOG_FILE
    parser = argparse.ArgumentParser(prog="mcp-lazy", description=__doc__.split("\n")[0])
    parser.add_argument("--name", required=True, help="short name for logs and cache files")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--log-file", default=None,
                        help="also append log lines, with a time stamp, to this file")
    parser.add_argument("--probe-timeout", type=float, default=20.0,
                        help="seconds to wait for the real server to start (default 20)")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give the real server command after --")
    if not all(c.isalnum() or c in "-_." for c in args.name):
        parser.error("--name may use letters, digits, '-', '_' and '.' only")
    NAME = args.name
    LOG_FILE = args.log_file
    proxy = Proxy(args.name, command, args.cache_dir or default_cache_dir(), args.probe_timeout,
                  sys.stdin.buffer, sys.stdout.buffer)

    def stop(signum, _frame):
        proxy.finish(0, f"signal {signal.Signals(signum).name}")

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, stop)
    proxy.run()


if __name__ == "__main__":
    main()
