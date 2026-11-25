import argparse
import sys
import time

import state
import udp_discovery


def print_help():
    """Displays all available console commands."""
    with state.screen_lock:
        print("Available commands:")
        print("  /list                 - Show online users")
        print("  /chat <name|number>   - Start chatting with a user")
        print("  /send <file>          - Send a file to the current chat partner")
        print("  /leave                - Close the current chat")
        print("  /status <online|away> - Change your status")
        print("  /help                 - Show this help message")
        print("  /quit                 - Exit the program")


def handle_list():
    """Prints the current list of online peers from shared state."""
    peers = state.get_peers_snapshot()
    with state.screen_lock:
        if not peers:
            print("[*] No other users online right now.")
            return

        for idx, (name, info) in enumerate(peers.items(), start=1):
            print(f"  {idx}. {name:<10} ({info['ip']}:{info['port']})  {info['status']}")


def handle_status(parts):
    """Updates the local user's status (online or away)."""
    if len(parts) != 2 or parts[1].lower() not in ("online", "away"):
        with state.screen_lock:
            print("[!] Usage: /status <online|away>")
        return

    new_status = parts[1].lower()
    with state.state_lock:
        state.my_status = new_status
    with state.screen_lock:
        print(f"[*] Status changed to: {state.my_status}")


def main():
    parser = argparse.ArgumentParser(description="LAN Chat & File Sharing (P2P)")
    parser.add_argument("--username", required=True, help="Your unique username (2-20 chars)")
    parser.add_argument("--port", type=int, default=5000, help="Your TCP listening port (1024-65535)")
    parser.add_argument("--bcast", default="255.255.255.255", help="UDP broadcast IPv4")
    args = parser.parse_args()

    try:
        username, port, bcast_ip = udp_discovery.validate_startup_args(
            args.username, args.port, args.bcast
        )
    except ValueError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    state.running = True
    state.my_name = ""
    state.my_tcp_port = port

    print("[*] Scanning LAN for active peers...")
    try:
        udp_discovery.start_listener(broadcast_ip=bcast_ip)
    except RuntimeError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    try:
        if udp_discovery.is_username_taken(username):
            print(f"[!] Username '{username}' is already taken on the network. Please restart with another --username.")
            sys.exit(1)

        conflict_peer = udp_discovery.find_same_machine_port_conflict(port)
        if conflict_peer is not None:
            print(
                f"[!] TCP port {port} is already used by '{conflict_peer}' on this machine. "
                f"Please restart with another --port."
            )
            sys.exit(1)

        with state.state_lock:
            state.my_name = username

        udp_discovery.start_broadcaster()
        # TODO (Khoa): Start TCP acceptor thread here

        with state.screen_lock:
            print(f"[*] Welcome {state.my_name}! Listening on TCP port {state.my_tcp_port}")
            print("[*] Type /help to see available commands.")

        # Main input loop (Member B will later upgrade this with prompt_toolkit)
        while state.running:
            try:
                user_input = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                state.running = False
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                parts = user_input.split()
                cmd = parts[0].lower()

                if cmd == "/help":
                    print_help()
                elif cmd == "/list":
                    handle_list()
                elif cmd == "/status":
                    handle_status(parts)
                elif cmd == "/chat":
                    print("[*] /chat feature is coming in Week 2 (TCP module).")
                elif cmd == "/leave":
                    print("[*] /leave feature is coming in Week 2 (TCP module).")
                elif cmd == "/send":
                    print("[*] /send feature is coming in Week 2 (File module).")
                elif cmd == "/quit":
                    state.running = False
                    print("[*] Shutting down... Bye!")
                else:
                    print("[!] Unknown command. Type /help for a list of commands.")
            else:
                if state.current_partner is None:
                    print("[!] You are not in a chat. Use /list and /chat <name> first.")
                else:
                    print(f"[{state.my_name}] {user_input}")
                    # TODO (Khoa): Send this message via TCP
    except KeyboardInterrupt:
        state.running = False
    except RuntimeError as exc:
        print(f"[!] {exc}")
        state.running = False
    finally:
        udp_discovery.stop_discovery()


if __name__ == "__main__":
    main()