"""A small stdio MCP server for the mcp-lazy tests.

Behaviour is set with environment variables:
  FAKE_LOG          file that gets one JSON line per event (start, recv, exit)
  FAKE_TOOLS        comma-separated tool names (default "echo,ask_roots,big,crash")
  FAKE_PAGE_SIZE    tools per tools/list page (default: all on one page)
  FAKE_FAIL         "exit" exits at once; "refuse" answers initialize with an error
  FAKE_IGNORE_EOF   keep running after end of input
  FAKE_IGNORE_TERM  ignore SIGTERM
  FAKE_GRANDCHILD   start a long `sleep` child (to test process-tree cleanup)
"""
import json
import os
import signal
import subprocess
import sys
import time

LOG = os.environ.get("FAKE_LOG")


def event(**fields):
    if LOG:
        with open(LOG, "a") as handle:
            handle.write(json.dumps(dict(fields, pid=os.getpid())) + "\n")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main():
    event(event="start", argv=sys.argv[1:])
    if os.environ.get("FAKE_IGNORE_TERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if os.environ.get("FAKE_FAIL") == "exit":
        event(event="exit", reason="FAKE_FAIL")
        sys.exit(3)
    grandchild = None
    if os.environ.get("FAKE_GRANDCHILD"):
        grandchild = subprocess.Popen(["sleep", "300"])
        event(event="grandchild", child=grandchild.pid)
    names = os.environ.get("FAKE_TOOLS", "echo,ask_roots,big,crash").split(",")
    tools = [{"name": n, "description": f"fake {n}", "inputSchema": {"type": "object"}} for n in names]
    page_size = int(os.environ.get("FAKE_PAGE_SIZE", "0")) or len(tools)
    pending_roots = {}
    next_id = 1
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        event(event="recv", method=method, id=message.get("id"),
              params=message.get("params") if method == "initialize" else None)
        if method is None:  # a response from the client
            call = pending_roots.pop(message.get("id"), None)
            if call is not None:
                send({"jsonrpc": "2.0", "id": call,
                      "result": {"content": [{"type": "text", "text": json.dumps(message.get("result"))}]}})
            continue
        if "id" not in message:
            continue
        rid = message["id"]
        if method == "initialize":
            if os.environ.get("FAKE_FAIL") == "refuse":
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "refused"}})
                continue
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": message["params"].get("protocolVersion"),
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "fake", "version": "1"}}})
        elif method == "tools/list":
            start = int((message.get("params") or {}).get("cursor") or 0)
            result = {"tools": tools[start:start + page_size]}
            if start + page_size < len(tools):
                result["nextCursor"] = str(start + page_size)
            send({"jsonrpc": "2.0", "id": rid, "result": result})
        elif method == "tools/call":
            name = message["params"]["name"]
            args = message["params"].get("arguments") or {}
            if name == "echo":
                send({"jsonrpc": "2.0", "method": "notifications/message",
                      "params": {"level": "info", "data": "echo called"}})
                send({"jsonrpc": "2.0", "id": rid,
                      "result": {"content": [{"type": "text", "text": args.get("text", "")}]}})
            elif name == "ask_roots":
                ask = f"srv-{next_id}"
                next_id += 1
                pending_roots[ask] = rid
                send({"jsonrpc": "2.0", "id": ask, "method": "roots/list"})
            elif name == "big":
                send({"jsonrpc": "2.0", "id": rid,
                      "result": {"content": [{"type": "text", "text": "x" * int(args.get("size", 2_000_000))}]}})
            elif name == "crash":
                event(event="exit", reason="crash")
                os._exit(7)
            else:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "unknown tool"}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}})
    if os.environ.get("FAKE_IGNORE_EOF"):
        event(event="eof-ignored")
        while True:
            time.sleep(1)
    if grandchild is not None:
        grandchild.terminate()
    event(event="exit", reason="eof")


if __name__ == "__main__":
    main()
