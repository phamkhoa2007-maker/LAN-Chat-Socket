import argparse
import socket
import threading
import time
from typing import Callable, Optional

import state


class UDPDiscovery:
    def __init__(
        self,
        udp_port: int = 9999,
        broadcast_ip: str = "255.255.255.255",
        broadcast_interval: float = 3.0,
        timeout: float = 10.0,
        on_notify: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._udp_port = udp_port
        self._broadcast_ip = broadcast_ip
        self._broadcast_interval = broadcast_interval
        self._timeout = timeout
        self._on_notify = on_notify
        self._send_sock: Optional[socket.socket] = None
        self._recv_sock: Optional[socket.socket] = None
        self._threads: list[threading.Thread] = []

    def start(self) -> list[threading.Thread]:
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._recv_sock.bind(("", self._udp_port))
        self._recv_sock.settimeout(1.0)

        broadcaster = threading.Thread(target=self._broadcast_loop, name="UDPBroadcaster", daemon=True)
        listener = threading.Thread(target=self._listen_loop, name="UDPListener", daemon=True)
        broadcaster.start()
        listener.start()
        self._threads = [broadcaster, listener]
        return self._threads

    def stop(self) -> None:
        state.running = False
        for sock in (self._send_sock, self._recv_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        for thread in self._threads:
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
                    name = state.my_name
                    tcp_port = state.my_tcp_port
                    status = state.my_status

                if name and self._send_sock is not None:
                    payload = f"HELLO|{name}|{tcp_port}|{status}".encode("utf-8")
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

        peer_name, port_str, peer_status = parts[1], parts[2], parts[3]
        if not peer_name or peer_name == state.my_name:
            return
        if peer_status not in ("online", "away"):
            return

        try:
            peer_port = int(port_str)
        except ValueError:
            return

        previous = state.get_peers_snapshot().get(peer_name)
        is_new = state.update_peer(peer_name, sender_ip, peer_port, peer_status)

        if is_new:
            self._notify(f"[*] {peer_name} is online ({sender_ip}:{peer_port})")
        elif previous is not None and previous["status"] != peer_status:
            self._notify(f"[*] {peer_name} is now {peer_status}")

    def _sweep_timeouts(self) -> None:
        removed_peers = state.remove_timed_out_peers(self._timeout)
        for peer_name in removed_peers:
            self._notify(f"[*] {peer_name} is offline")


def start_udp_discovery(
    broadcast_ip: str = "255.255.255.255",
    udp_port: int = 9999,
    broadcast_interval: float = 3.0,
    timeout: float = 10.0,
    on_notify: Optional[Callable[[str], None]] = None,
) -> UDPDiscovery:
    discovery = UDPDiscovery(
        udp_port=udp_port,
        broadcast_ip=broadcast_ip,
        broadcast_interval=broadcast_interval,
        timeout=timeout,
        on_notify=on_notify,
    )
    discovery.start()
    return discovery


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--bcast", default="255.255.255.255")
    args = parser.parse_args()

    state.my_name = args.name
    state.my_tcp_port = args.port

    discovery = start_udp_discovery(broadcast_ip=args.bcast)
    with state.screen_lock:
        print(f"[*] Discovery running as {state.my_name} (TCP port {state.my_tcp_port})")

    try:
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
                if len(parts) < 2 or parts[1] not in ("online", "away"):
                    with state.screen_lock:
                        print("[!] Usage: /status <online|away>")
                else:
                    with state.state_lock:
                        state.my_status = parts[1]
                    with state.screen_lock:
                        print(f"[*] Status changed to: {state.my_status}")
            elif cmd == "/quit":
                state.running = False
            else:
                with state.screen_lock:
                    print("[!] Commands: /list, /status <online|away>, /quit")
    except (KeyboardInterrupt, EOFError):
        state.running = False
    finally:
        discovery.stop()


if __name__ == "__main__":
    main()