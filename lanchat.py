import argparse
import state


def print_help():
    """Displays all available console commands."""
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
    if not peers:
        print("[*] No other users online right now.")
        return

    for idx, (name, info) in enumerate(peers.items(), start=1):
        print(f"  {idx}. {name:<10} ({info['ip']}:{info['port']})  {info['status']}")


def handle_status(parts):
    """Updates the local user's status (online or away)."""
    if len(parts) < 2 or parts[1] not in ("online", "away"):
        print("[!] Usage: /status <online|away>")
        return

    with state.state_lock:
        state.my_status = parts[1]
    print(f"[*] Status changed to: {state.my_status}")


def main():
    # Parse command-line arguments: python lanchat.py --name Khoa --port 5001
    parser = argparse.ArgumentParser(description="LAN Chat & File Sharing (P2P)")
    parser.add_argument("--name", required=True, help="Your display name")
    parser.add_argument("--port", type=int, default=5000, help="Your TCP listening port")
    args = parser.parse_args()

    state.my_name = args.name
    state.my_tcp_port = args.port

    print(f"[*] Welcome {state.my_name}! Listening on TCP port {state.my_tcp_port}")
    print("[*] Type /help to see available commands.")

    # TODO (Member A): Start UDP broadcaster & listener threads here
    # TODO (Khoa): Start TCP acceptor thread here

    # Main input loop (Member B will later upgrade this with prompt_toolkit)
    while state.running:
        try:
            user_input = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
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
            # Normal text message
            if state.current_partner is None:
                print("[!] You are not in a chat. Use /list and /chat <name> first.")
            else:
                print(f"[{state.my_name}] {user_input}")
                # TODO (Khoa): Send this message via TCP


if __name__ == "__main__":
    main()

# use to test lanchat.py (python lanchat.py --name Khoa --port 5001)