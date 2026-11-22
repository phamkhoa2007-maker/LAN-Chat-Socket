import threading
import time
import state

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False


# ==========================================
# 1. THREAD-SAFE CONSOLE OUTPUT HELPERS
# ==========================================
def safe_print(text: str = "") -> None:
    """Prints text to console safely across multiple threads using screen_lock."""
    with state.screen_lock:
        print(text, flush=True)


def print_system_notice(message: str, level: str = "INFO") -> None:
    """
    Prints standardized system notifications (online/offline, errors, unread alerts).
    Matches lanchat.py prefix conventions: [*] for info, [!] for warnings/errors.
    """
    prefix = "[!]" if level.upper() in ("WARN", "ERROR") else "[*]"
    safe_print(f"{prefix} {message}")


def print_chat_message(sender: str, message: str) -> None:
    """Prints a formatted chat message safely."""
    safe_print(f"[{sender}] {message}")


def print_peer_table() -> None:
    """
    Displays a formatted, aligned table of online peers (/list command),
    including unread message counts if any.
    """
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
# 2. ANTI-GARBLE INPUT SESSION (PROMPT_TOOLKIT)
# ==========================================
def create_prompt_session():
    """Creates a PromptSession if prompt_toolkit is available, otherwise returns None."""
    if HAS_PROMPT_TOOLKIT:
        return PromptSession()
    return None


def read_user_input(session=None, prompt_text: str = "> ") -> str:
    """Reads a line of input without letting background prints break the typed line."""
    if session is not None:
        return session.prompt(prompt_text)
    return input(prompt_text)


# ==========================================
# 3. STANDALONE TEST FOR MEMBER B
# ==========================================
if __name__ == "__main__":
    # 1. Populate sample peers into state.py to test /list table
    state.update_peer("Khoa", "192.168.1.10", 5001, "online")
    state.update_peer("Minh", "192.168.1.12", 5002, "away")
    with state.state_lock:
        state.unread_counts["Minh"] = 3

    print_system_notice("Testing thread-safe table & anti-garble input...")
    print_peer_table()

    # 2. Simulate background network messages arriving every 3 seconds while you type
    stop_demo = threading.Event()

    def background_spammer():
        count = 1
        while not stop_demo.is_set():
            time.sleep(3)
            if stop_demo.is_set():
                break
            print_system_notice(f"Background UDP/TCP event #{count} arrived while you are typing!")
            count += 1

    bg_thread = threading.Thread(target=background_spammer, daemon=True)
    bg_thread.start()

    print_system_notice("Try typing a long sentence slowly without pressing Enter (type /quit to exit):")
    session = create_prompt_session()

    if HAS_PROMPT_TOOLKIT:
        with patch_stdout():
            while True:
                try:
                    text = read_user_input(session, "> ").strip()
                    if text == "/quit":
                        break
                    if text == "/list":
                        print_peer_table()
                    elif text:
                        print_chat_message("Me", text)
                except (KeyboardInterrupt, EOFError):
                    break
    else:
        print_system_notice("prompt_toolkit not installed, running basic input().", "WARN")

    stop_demo.set()
    print_system_notice("UI test finished.")