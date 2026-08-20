"""Launcher for the WWP dashboard server.

Exists because ``uvicorn server.app:app --port 8000`` binds IPv4 ``127.0.0.1``
only, while on Windows ``localhost`` resolves to IPv6 ``::1`` first — so
http://localhost:8000 is refused even though the server is up. This binds a
dual-stack socket that answers on both, finds a free port if the one you asked
for is taken, and prints the URL that actually works.

    python server/run.py                 # http://localhost:8000
    python server/run.py --port 8080
    python server/run.py --reload        # auto-restart on edit (IPv4 host)
    python server/run.py --check         # diagnose without starting

Set WWP_PROVIDER=wapor for real FAO WaPOR v3 retrieval (needs the geo stack).
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Allow `python server/run.py` from anywhere: the app is imported as server.app.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check_deps() -> list[str]:
    missing = []
    for mod, pip_name in (("fastapi", "fastapi"), ("uvicorn", "uvicorn[standard]"),
                          ("pydantic", "pydantic"), ("shapefile", "pyshp")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    return missing


def port_busy(port: int) -> bool:
    """True if anything is already listening on this port.

    Probing by CONNECT, not by bind: on Windows SO_REUSEADDR lets a second
    socket bind a port that is already listening, so a bind probe reports a
    busy port as free and you end up with two servers quietly sharing it.
    """
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.35)
                if probe.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def free_port(port: int, tries: int = 20) -> int:
    """Return `port`, or the next free one, so a stale server is not fatal."""
    for candidate in range(port, port + tries):
        if not port_busy(candidate):
            return candidate
    raise SystemExit(f"no free port in {port}..{port + tries - 1}")


def dual_stack_socket(port: int) -> tuple[socket.socket, bool]:
    """A listening socket that answers on both ::1 and 127.0.0.1 where possible."""
    def _prepare(sock: socket.socket) -> None:
        # Deliberately NOT SO_REUSEADDR on Windows: there it permits hijacking a
        # live listener, turning a port clash into two servers on one port
        # instead of a clear error.
        if sys.platform == "win32":
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        _prepare(sock)
        # 0 = accept IPv4-mapped addresses too. Windows and most Linuxes default
        # this to 1 (IPv6 only), which is exactly what breaks `localhost`.
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", port))
        sock.listen(128)
        sock.set_inheritable(True)
        return sock, True
    except OSError:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _prepare(sock)
        sock.bind(("0.0.0.0", port))
        sock.listen(128)
        sock.set_inheritable(True)
        return sock, False


def resolve_localhost(port: int) -> list[str]:
    out = []
    try:
        for fam, _, _, _, addr in socket.getaddrinfo("localhost", port, type=socket.SOCK_STREAM):
            out.append(("IPv6" if fam == socket.AF_INET6 else "IPv4") + " " + str(addr[0]))
    except OSError as exc:
        out.append(f"resolution failed: {exc}")
    return out


def diagnose(port: int) -> None:
    print("WWP dashboard server - diagnostics\n")
    print(f"  project root      {ROOT}")
    print(f"  dashboard present {(ROOT / 'wheat_dashboard.html').exists()}")
    print(f"  Data/ present     {(ROOT / 'Data').exists()}")
    print(f"  python            {sys.version.split()[0]}  ({sys.executable})")
    missing = check_deps()
    print(f"  dependencies      {'MISSING: ' + ', '.join(missing) if missing else 'ok'}")
    print(f"  WWP_PROVIDER      {os.environ.get('WWP_PROVIDER', '(unset -> synthetic)')}")
    print(f"  localhost:{port} resolves to")
    for line in resolve_localhost(port):
        print(f"                    {line}")
    chosen = free_port(port)
    print(f"  port {port}         {'free' if chosen == port else f'IN USE (next free: {chosen})'}")
    if missing:
        print("\n  fix: pip install -r server/requirements.txt")


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the WWP dashboard and its API.")
    # PORT is what Railway (and most PaaS) inject; WWP_PORT stays for local use.
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT") or os.environ.get("WWP_PORT") or 8000))
    ap.add_argument("--host", default=None,
                    help="bind a specific host instead of dual-stack (e.g. 0.0.0.0)")
    ap.add_argument("--reload", action="store_true",
                    help="restart on file changes; binds --host (default 127.0.0.1)")
    ap.add_argument("--check", action="store_true", help="print diagnostics and exit")
    args = ap.parse_args()

    if args.check:
        diagnose(args.port)
        return

    missing = check_deps()
    if missing:
        print("Missing dependencies: " + ", ".join(missing), file=sys.stderr)
        print("Install them with:\n  pip install -r server/requirements.txt", file=sys.stderr)
        raise SystemExit(1)

    if not (ROOT / "wheat_dashboard.html").exists():
        print(f"wheat_dashboard.html not found in {ROOT}", file=sys.stderr)
        raise SystemExit(1)

    import uvicorn

    if os.environ.get("PORT"):
        # A platform assigned this port; silently moving off it would make the
        # service unreachable behind the router.
        port = args.port
    else:
        port = free_port(args.port)
        if port != args.port:
            print(f"[wwp] port {args.port} is in use - using {port} instead", flush=True)

    # --reload needs uvicorn to own the socket so it can hand it to the child,
    # so that path takes a plain host/port and loses dual-stack.
    if args.reload:
        host = args.host or "127.0.0.1"
        os.environ.setdefault("WWP_SELECTOR_LOOP", "1")
        print(f"[wwp] reload mode on {host}:{port} - open http://{host}:{port}/", flush=True)
        if host == "127.0.0.1":
            print("[wwp] note: use 127.0.0.1, not localhost - reload mode binds IPv4 only", flush=True)
        uvicorn.run("server.app:app", host=host, port=port, reload=True,
                    reload_dirs=[str(ROOT / "server")])
        return

    if args.host:
        print(f"[wwp] listening on {args.host}:{port} - open http://{args.host}:{port}/", flush=True)
        uvicorn.run("server.app:app", host=args.host, port=port)
        return

    # Windows: the default Proactor loop aborts its whole accept loop with
    # WinError 64 when a client drops mid-handshake ("Accept failed on a socket"),
    # and the server then silently stops answering. The selector loop does not,
    # and its limits are irrelevant for a dev server.
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    sock, dual = dual_stack_socket(port)
    print(f"[wwp] listening on {'[::] (dual-stack IPv4 + IPv6)' if dual else '0.0.0.0 (IPv4)'} port {port}", flush=True)
    print(f"[wwp] open  http://localhost:{port}/   or   http://127.0.0.1:{port}/", flush=True)
    config = uvicorn.Config("server.app:app", log_level="info")
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
