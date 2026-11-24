import argparse
import ipaddress
import re
import socket
import sys
import threading
import time
from typing import Callable, Optional

import state

USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,19}$")
MIN_PORT = 1024
MAX_PORT = 65535


def validate_username(raw_username: str) -> str:
    username = raw_username.strip().lower()
    if not USERNAME_PATTERN.match(username):
        raise ValueError(
            "Invalid username. Use 2-20 lowercase letters, digits, or '_', "
            "starting with a letter (no spaces or special characters)."
        )
    return username


def validate_port(port: int) -> int:
    if not isinstance(port, int) or isinstance(port, bool):
        raise ValueError("Port must be an integer.")
    if port < MIN_PORT or port > MAX_PORT:
        raise ValueError(f"Invalid port ({port}). Choose a port between {MIN_PORT} and {MAX_PORT}.")
    return port


def validate_broadcast_ip(bcast_ip: str) -> str:
    cleaned = bcast_ip.strip()
    try:
        ipaddress.IPv4Address(cleaned)
    except ipaddress.AddressValueError as exc:
        raise ValueError(f"Invalid IPv4 broadcast address: '{bcast_ip}'.") from exc
    return cleaned


def get_local_ips() -> set[str]:
    ips = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tmp:
            tmp.connect(("8.8.8.8", 80))
            ips.add(tmp.getsockname()[0])
    except OSError:
        pass
    return ips


def is_local_tcp_port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_sock:
            test_sock.bind(("", port))
        return True
    except OSError:
        return False


def is_username_taken(username: str) -> bool:
    target = username.strip().lower()
    peers = state.get_peers_snapshot()
    return any(peer_name.lower() == target for peer_name in peers)


def find_same_machine_port_conflict(port: int) -> Optional[str]:
    local_ips = get_local_ips()
    peers = state.get_peers_snapshot()
    for peer_name, info in peers.items():
        if info["ip"] in local_ips and info["port"] == port:
            return peer_name
    return None


class UDPDiscovery:
    def __init__(
        self,
        udp_port: int = 9999,
        broadcast_ip: str = "255.255.255.255",
        broadcast_interval: float = 3.0,
        timeout: float = 10.0,
        on_notify: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._udp_port = validate_port(udp_port)
        self._broadcast_ip = validate_broadcast_ip(broadcast_ip)
        self._broadcast_interval = broadcast_interval
        self._timeout = timeout
        self._on_notify = on_notify

        self._local_ips = get_local_ips()
        self._warned_collisions: set[tuple[str, int]] = set()

        self._send_sock: Optional[socket.socket] = None
        self._recv_sock: Optional[socket.socket] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._broadcaster_thread: Optional[threading.Thread] = None

    def start_listener(self) -> threading.Thread:
        if self._listener_thread is not None and self._listener_thread.is_alive():
            return self._listener_thread

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", self._udp_port))
            sock.settimeout(1.0)
        except OSError as exc:
            sock.close()
            raise RuntimeError(
                f"Failed to bind UDP port {self._udp_port} "
                f"(port may be in use or blocked by firewall): {exc}"
            ) from exc

        self._recv_sock = sock
        self._listener_thread = threading.Thread(
            target=self._listen_loop,
            name="UDPListener",
            daemon=True,
        )
        self._listener_thread.start()
        return self._listener_thread

    def start_broadcaster(self) -> threading.Thread:
        if self._broadcaster_thread is not None and self._broadcaster_thread.is_alive():
            return self._broadcaster_thread

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError as exc:
            sock.close()
            raise RuntimeError(f"Failed to enable UDP broadcast: {exc}") from exc

        self._send_sock = sock
        self._broadcaster_thread = threading.Thread(
            target=self._broadcast_loop,
            name="UDPBroadcaster",
            daemon=True,
        )
        self._broadcaster_thread.start()
        return self._broadcaster_thread

    def stop(self) -> None:
        state.running = False
        for sock in (self._send_sock, self._recv_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        for thread in (self._broadcaster_thread, self._listener_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)

    def _notify(self, message: str) -> None:
        if self._on_notify is not None:
            self._on_notify(message)
            return
        with state.screen_lock:
            print(f"\n{message}")

    def _sleep_interruptible(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while state.running and time.time() < deadline:
            time.sleep(min(0.2, deadline - time.time()))

    def _broadcast_loop(self) -> None:
        try:
            while state.running:
                with state.state_lock:
                    username = state.my_name
                    tcp_port = state.my_tcp_port
                    status = state.my_status

                if username and self._send_sock is not None:
                    payload = f"HELLO|{username}|{tcp_port}|{status}".encode("utf-8")
                    try:
                        self._send_sock.sendto(payload, (self._broadcast_ip, self._udp_port))
                    except OSError:
                        pass

                self._sleep_interruptible(self._broadcast_interval)
        finally:
            if self._send_sock is not None:
                try:
                    self._send_sock.close()
                except OSError:
                    pass

    def _listen_loop(self) -> None:
        last_sweep = time.time()
        try:
            while state.running:
                if self._recv_sock is None:
                    break
                try:
                    data, addr = self._recv_sock.recvfrom(1024)
                    self._handle_packet(data, addr[0])
                except socket.timeout:
                    pass
                except OSError:
                    break

                now = time.time()
                if now - last_sweep >= 1.0:
                    last_sweep = now
                    self._sweep_timeouts()
        finally:
            if self._recv_sock is not None:
                try:
                    self._recv_sock.close()
                except OSError:
                    pass

    def _handle_packet(self, data: bytes, sender_ip: str) -> None:
        parts = data.decode("utf-8", errors="replace").strip().split("|")
        if len(parts) != 4 or parts[0] != "HELLO":
            return

        peer_username = parts[1].strip().lower()
        port_str = parts[2].strip()
        peer_status = parts[3].strip().lower()

        if not USERNAME_PATTERN.match(peer_username):
            return
        if peer_status not in ("online", "away"):
            return

        try:
            peer_port = int(port_str)
            validate_port(peer_port)
        except ValueError:
            return

        with state.state_lock:
            my_username = state.my_name.lower()
            my_port = state.my_tcp_port

        if my_username and peer_username == my_username:
            is_self = (peer_port == my_port) and (sender_ip in self._local_ips)
            if not is_self and (sender_ip, peer_port) not in self._warned_collisions:
                self._warned_collisions.add((sender_ip, peer_port))
                self._notify(
                    f"[!] WARNING: Another peer ({sender_ip}:{peer_port}) is already using "
                    f"username '{my_username}'. Please /quit and restart with a different --username."
                )
            return

        previous = state.get_peers_snapshot().get(peer_username)
        is_new = state.update_peer(peer_username, sender_ip, peer_port, peer_status)

        if is_new:
            self._notify(f"[*] {peer_username} is online ({sender_ip}:{peer_port})")
        elif previous is not None and previous["status"] != peer_status:
            self._notify(f"[*] {peer_username} is now {peer_status}")

    def _sweep_timeouts(self) -> None:
        removed_peers = state.remove_timed_out_peers(self._timeout)
        for peer_username in removed_peers:
            self._notify(f"[*] {peer_username} is offline")


_default_discovery: Optional[UDPDiscovery] = None


def start_listener(
    broadcast_ip: str = "255.255.255.255",
    udp_port: int = 9999,
    broadcast_interval: float = 3.0,
    timeout: float = 10.0,
    on_notify: Optional[Callable[[str], None]] = None,
) -> threading.Thread:
    global _default_discovery
    if _default_discovery is None:
        _default_discovery = UDPDiscovery(
            udp_port=udp_port,
            broadcast_ip=broadcast_ip,
            broadcast_interval=broadcast_interval,
            timeout=timeout,
            on_notify=on_notify,
        )
    return _default_discovery.start_listener()


def start_broadcaster() -> threading.Thread:
    global _default_discovery
    if _default_discovery is None:
        _default_discovery = UDPDiscovery()
    return _default_discovery.start_broadcaster()


def stop_discovery() -> None:
    global _default_discovery
    if _default_discovery is not None:
        _default_discovery.stop()
        _default_discovery = None


def validate_startup_args(raw_username: str, port: int, bcast_ip: str) -> tuple[str, int, str]:
    username = validate_username(raw_username)
    valid_port = validate_port(port)
    valid_bcast = validate_broadcast_ip(bcast_ip)
    if not is_local_tcp_port_available(valid_port):
        raise ValueError(f"TCP port {valid_port} is already in use on this machine. Please choose another --port.")
    return username, valid_port, valid_bcast


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP Peer Discovery (Standalone Test)")
    parser.add_argument("--username", required=True, help="Unique username (lowercase, 2-20 chars)")
    parser.add_argument("--port", type=int, default=5000, help="Your TCP listening port (1024-65535)")
    parser.add_argument("--bcast", default="255.255.255.255", help="UDP broadcast IPv4")
    args = parser.parse_args()

    try:
        username, port, bcast_ip = validate_startup_args(args.username, args.port, args.bcast)
    except ValueError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    state.my_name = ""
    state.my_tcp_port = port

    print("[*] Scanning LAN for active peers (3.5s)...")
    try:
        start_listener(broadcast_ip=bcast_ip)
    except RuntimeError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    try:
        time.sleep(3.5)

        if is_username_taken(username):
            print(f"[!] Username '{username}' is already taken on the network. Please restart with another --username.")
            stop_discovery()
            sys.exit(1)

        conflict_peer = find_same_machine_port_conflict(port)
        if conflict_peer is not None:
            print(
                f"[!] TCP port {port} is already used by '{conflict_peer}' on this machine. "
                f"Please restart with another --port."
            )
            stop_discovery()
            sys.exit(1)

        with state.state_lock:
            state.my_name = username

        start_broadcaster()

        with state.screen_lock:
            print(f"[*] Discovery running as '{state.my_name}' (TCP port {state.my_tcp_port})")

        while state.running:
            command = input("> ").strip()
            if not command:
                continue
            parts = command.split()
            cmd = parts[0].lower()

            if cmd == "/list":
                peers = state.get_peers_snapshot()
                with state.screen_lock:
                    if not peers:
                        print("[*] No other users online right now.")
                    else:
                        for idx, (name, info) in enumerate(peers.items(), start=1):
                            print(f"  {idx}. {name:<10} ({info['ip']}:{info['port']})  {info['status']}")
            elif cmd == "/status":
                if len(parts) != 2 or parts[1].lower() not in ("online", "away"):
                    with state.screen_lock:
                        print("[!] Usage: /status <online|away>")
                else:
                    with state.state_lock:
                        state.my_status = parts[1].lower()
                    with state.screen_lock:
                        print(f"[*] Status changed to: {state.my_status}")
            elif cmd == "/quit":
                state.running = False
            else:
                with state.screen_lock:
                    print("[!] Commands: /list, /status <online|away>, /quit")
    except (KeyboardInterrupt, EOFError):
        state.running = False
    except RuntimeError as exc:
        print(f"[!] {exc}")
        state.running = False
    finally:
        stop_discovery()


if __name__ == "__main__":
    main()