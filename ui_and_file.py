import os
import socket
import threading
import time
import state

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

CHUNK_SIZE = 4096
DOWNLOAD_DIR = "downloads"

# Per-socket lock so file chunks and chat frames never interleave on the same TCP stream
_socket_send_locks = {}
_locks_guard = threading.Lock()


def _get_socket_lock(sock: socket.socket) -> threading.Lock:
    """Returns a dedicated send lock for a given TCP socket."""
    with _locks_guard:
        sock_id = id(sock)
        if sock_id not in _socket_send_locks:
            _socket_send_locks[sock_id] = threading.Lock()
        return _socket_send_locks[sock_id]


# ==========================================
# 1. THREAD-SAFE CONSOLE OUTPUT HELPERS
# ==========================================
def safe_print(text: str = "") -> None:
    """Prints text to console safely across multiple threads using screen_lock."""
    with state.screen_lock:
        print(text, flush=True)


def print_system_notice(message: str, level: str = "INFO") -> None:
    """Prints standardized system notifications ([*] for info, [!] for warnings/errors)."""
    prefix = "[!]" if level.upper() in ("WARN", "ERROR") else "[*]"
    safe_print(f"{prefix} {message}")


def print_chat_message(sender: str, message: str) -> None:
    """Prints a formatted chat message safely."""
    safe_print(f"[{sender}] {message}")


def print_peer_table() -> None:
    """Displays a formatted table of online peers (/list), including unread counts."""
    peers = state.get_peers_snapshot()
    with state.state_lock:
        unread_snapshot = dict(state.unread_counts)

    with state.screen_lock:
        print("\n" + "=" * 68, flush=True)
        print(
            f"{'No.':<4} | {'Name':<14} | {'IP Address':<16} | {'Port':<6} | {'Status':<8} | {'Unread':<6}",
            flush=True,
        )
        print("-" * 68, flush=True)

        if not peers:
            print("  [*] No other users online right now.", flush=True)
        else:
            for idx, (name, info) in enumerate(peers.items(), start=1):
                ip = str(info.get("ip", "0.0.0.0"))
                port = str(info.get("port", "-"))
                status = str(info.get("status", "online"))
                unread = unread_snapshot.get(name, 0)
                unread_str = str(unread) if unread > 0 else "-"

                print(
                    f"{idx:<4} | {name:<14} | {ip:<16} | {port:<6} | {status:<8} | {unread_str:<6}",
                    flush=True,
                )

        print("=" * 68 + "\n", flush=True)


# ==========================================
# 2. CHAT HISTORY & UNREAD COUNTER
# ==========================================
def handle_incoming_message(sender: str, message: str) -> None:
    """
    Called by TCP Receiver Thread when a MSG frame arrives.
    Saves message to chat_history, displays immediately if chatting with `sender`,
    or increments unread_counts and displays a notice otherwise.
    """
    formatted_line = f"[{sender}] {message}"
    is_active_chat = False
    unread_total = 0

    with state.state_lock:
        state.chat_history.setdefault(sender, []).append(formatted_line)
        if state.current_partner == sender:
            is_active_chat = True
        else:
            unread_total = state.unread_counts.get(sender, 0) + 1
            state.unread_counts[sender] = unread_total

    if is_active_chat:
        print_chat_message(sender, message)
    else:
        print_system_notice(f"New message from {sender} ({unread_total} unread)")


def log_outgoing_message(recipient: str, message: str) -> None:
    """Saves an outgoing chat message to history and displays it on screen."""
    sender_name = state.my_name or "Me"
    formatted_line = f"[{sender_name}] {message}"

    with state.state_lock:
        state.chat_history.setdefault(recipient, []).append(formatted_line)

    print_chat_message(sender_name, message)


def open_chat_view(partner: str) -> None:
    """Switches active chat partner, resets unread counter, and replays chat history."""
    with state.state_lock:
        state.current_partner = partner
        state.unread_counts[partner] = 0
        history_snapshot = list(state.chat_history.get(partner, []))

    with state.screen_lock:
        print("\n" + "-" * 54, flush=True)
        print(f"[*] Chatting with {partner} (type /leave to return to lobby)", flush=True)
        print("-" * 54, flush=True)
        if not history_snapshot:
            print("  (No messages yet. Say hi!)", flush=True)
        else:
            for line in history_snapshot:
                print(line, flush=True)
        print("-" * 54 + "\n", flush=True)


# ==========================================
# 3. TCP FILE TRANSFER (SEND & RECEIVE)
# ==========================================
def _file_sender_worker(sock: socket.socket, filepath: str, partner_name: str) -> None:
    """Background thread worker that streams FILE|<filename>|<size>|<raw_bytes>."""
    filename = os.path.basename(filepath)
    try:
        file_size = os.path.getsize(filepath)
    except OSError as e:
        print_system_notice(f"Cannot access file '{filepath}': {e}", "ERROR")
        return

    header = f"FILE|{filename}|{file_size}|".encode("utf-8")
    send_lock = _get_socket_lock(sock)

    print_system_notice(f"Sending '{filename}' ({file_size} bytes) to {partner_name}...")

    try:
        with send_lock:
            sock.sendall(header)
            sent_bytes = 0
            last_milestone = 0

            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    sock.sendall(chunk)
                    sent_bytes += len(chunk)

                    if file_size > 0:
                        percent = int((sent_bytes / file_size) * 100)
                        if percent >= last_milestone + 20 and percent < 100:
                            last_milestone = (percent // 20) * 20
                            print_system_notice(f"Sending '{filename}': {last_milestone}%")

        print_system_notice(f"Sending '{filename}': 100% Done!")

    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        print_system_notice(
            f"File transfer failed for '{filename}' (connection lost: {e})", "ERROR"
        )


def start_file_send_thread(
    sock: socket.socket, filepath: str, partner_name: str = "Peer"
) -> bool:
    """
    Validates file existence and spawns a background File Sender Thread
    so chat remains responsive during transfer.
    """
    if sock is None:
        print_system_notice("No active TCP connection to send file.", "WARN")
        return False

    if not os.path.isfile(filepath):
        print_system_notice(f"File not found: '{filepath}'", "ERROR")
        return False

    t = threading.Thread(
        target=_file_sender_worker,
        args=(sock, filepath, partner_name),
        daemon=True,
    )
    t.start()
    return True


def receive_file_payload(
    sock: socket.socket, filename: str, file_size: int, sender_name: str = "Peer"
) -> bool:
    """
    Called by TCP Receiver Thread right after reading the 'FILE|<filename>|<size>|' header.
    Reads exact `file_size` bytes into `downloads/<filename>`.
    Deletes the incomplete file if the sender disconnects mid-transfer.
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    safe_name = os.path.basename(filename) or "received_file.bin"
    dest_path = os.path.join(DOWNLOAD_DIR, safe_name)

    print_system_notice(
        f"Receiving file '{safe_name}' ({file_size} bytes) from {sender_name}..."
    )

    received_bytes = 0
    last_milestone = 0
    transfer_ok = False

    try:
        with open(dest_path, "wb") as f:
            while received_bytes < file_size:
                to_read = min(CHUNK_SIZE, file_size - received_bytes)
                chunk = sock.recv(to_read)
                if not chunk:
                    raise ConnectionError("Peer disconnected mid-transfer")
                f.write(chunk)
                received_bytes += len(chunk)

                if file_size > 0:
                    percent = int((received_bytes / file_size) * 100)
                    if percent >= last_milestone + 20 and percent < 100:
                        last_milestone = (percent // 20) * 20
                        print_system_notice(f"Receiving '{safe_name}': {last_milestone}%")

        transfer_ok = True
        print_system_notice(
            f"Received '{safe_name}' (100%) -> saved to {dest_path}"
        )
        return True

    except Exception as e:
        print_system_notice(
            f"File receive aborted for '{safe_name}' ({e}). Cleaning up incomplete file.",
            "ERROR",
        )
        return False

    finally:
        # Must run AFTER `with open(...)` closes the handle (required on Windows)
        if not transfer_ok and os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass


# ==========================================
# 4. ANTI-GARBLE INPUT SESSION & LOOP
# ==========================================
def create_prompt_session():
    """Creates a PromptSession if prompt_toolkit is available."""
    if HAS_PROMPT_TOOLKIT:
        return PromptSession()
    return None


def read_user_input(session=None, prompt_text: str = "> ") -> str:
    """Reads a line of input without letting background prints break the typed line."""
    if session is not None:
        return session.prompt(prompt_text)
    return input(prompt_text)


def run_console_loop(command_handler) -> None:
    """
    Main anti-garble input loop that lanchat.py can delegate to.
    `command_handler(user_input: str)` handles parsed commands and chat messages.
    """
    session = create_prompt_session()
    if HAS_PROMPT_TOOLKIT:
        with patch_stdout():
            while state.running:
                try:
                    user_input = read_user_input(session, "> ").strip()
                except (KeyboardInterrupt, EOFError):
                    state.running = False
                    break
                if user_input:
                    command_handler(user_input)
    else:
        while state.running:
            try:
                user_input = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                state.running = False
                break
            if user_input:
                command_handler(user_input)


# ==========================================
# 5. STANDALONE END-TO-END TEST FOR MEMBER B
# ==========================================
if __name__ == "__main__":
    state.my_name = "Khoa"
    state.update_peer("Minh", "127.0.0.1", 5002, "online")
    state.update_peer("Huy", "127.0.0.1", 5003, "online")

    # Create a temporary ~150 KB sample file to test chunking and % progress
    sample_file = "sample_test_report.txt"
    with open(sample_file, "wb") as f:
        f.write(b"LAN Chat & File Sharing Test Data Line\n" * 4000)

    # Set up a real local TCP socket pair to simulate Minh receiving our file
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    test_port = server_sock.getsockname()[1]

    def mock_minh_receiver():
        conn, _ = server_sock.accept()
        with conn:
            # Read header up to the 3rd '|' character: FILE|<name>|<size>|
            header_bytes = bytearray()
            pipes = 0
            while pipes < 3:
                ch = conn.recv(1)
                if not ch:
                    return
                header_bytes.extend(ch)
                if ch == b"|":
                    pipes += 1

            parts = header_bytes.decode("utf-8").split("|")
            # parts = ["FILE", filename, size_str, ""]
            receive_file_payload(conn, parts[1], int(parts[2]), sender_name="Khoa")

    threading.Thread(target=mock_minh_receiver, daemon=True).start()

    client_sock = socket.create_connection(("127.0.0.1", test_port))
    open_chat_view("Minh")

    # Trigger file send in background while Huy sends unread messages
    start_file_send_thread(client_sock, sample_file, partner_name="Minh")
    time.sleep(0.1)
    handle_incoming_message("Huy", "Alo tui gui tin nhan luc dang truyen file ne!")

    print_system_notice(
        "Test commands: /list | /chat Huy | /chat Minh | /send sample_test_report.txt | /quit"
    )

    def demo_handler(cmd_text: str):
        if cmd_text == "/quit":
            state.running = False
        elif cmd_text == "/list":
            print_peer_table()
        elif cmd_text.startswith("/chat "):
            open_chat_view(cmd_text.split(maxsplit=1)[1])
        elif cmd_text.startswith("/send "):
            path = cmd_text.split(maxsplit=1)[1]
            start_file_send_thread(client_sock, path, partner_name="Minh")
        elif cmd_text == "/leave":
            with state.state_lock:
                state.current_partner = None
            print_system_notice("Returned to lobby.")
        else:
            if state.current_partner:
                log_outgoing_message(state.current_partner, cmd_text)
            else:
                print_system_notice("Use /chat <name> first.", "WARN")

    run_console_loop(demo_handler)

    # Clean up temporary test resources
    client_sock.close()
    server_sock.close()
    if os.path.exists(sample_file):
        os.remove(sample_file)
    print_system_notice("All Member B module tests finished.")