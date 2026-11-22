# P2P LAN Chat & File Sharing Application (TCP/UDP Socket)

A decentralized, command-line Peer-to-Peer (P2P) instant messaging and file-sharing application written in Python 3 for Local Area Networks (LAN / Wi-Fi). Every participant runs the exact same program (`lanchat.py`), acting simultaneously as both a TCP/UDP server and client without relying on any central server.

---

## Team Members & Module Ownership

| Member | Role | Assigned Files & Responsibilities |
| :--- | :--- | :--- |
| **Khoa** | Team Leader | `lanchat.py`, `state.py`, `tcp_transport.py` — System architecture, TCP Acceptor/Receiver threads, stream framing (`MSG\|`, `BYE\|`), `recv_exact()`, and module integration. |
| **Khang** | Member A | `udp_discovery.py` — UDP Broadcast presence discovery (`HELLO\|` every 3s on port `9999`), 10s peer timeout eviction, `/list` & `/status` logic, and Wireshark packet captures. |
| **Long** | Member B | `ui_and_file.py`, `README.md`, Project Report — Anti-garble Console UI (`prompt_toolkit` & `screen_lock`), chat history & unread message routing, and async chunked file transfer (`FILE\|`). |

---

## Key Features

- **Automatic Peer Discovery (UDP Broadcast):** Broadcasts `HELLO|<name>|<tcp_port>|<status>` to `255.255.255.255:9999` every 3 seconds and automatically removes peers that remain silent for more than 10 seconds.
- **Bidirectional Multi-Peer TCP Chat:** Establishes reliable unicast TCP connections (`SOCK_STREAM`) with byte-length framing (`MSG|<byte_len>|<text>`) to prevent message coalescing or truncation.
- **Multi-Peer Unread Message Routing:** Buffers incoming messages from background peers into per-user histories (`[*] New message from <peer> (N unread)`) without interrupting your active conversation.
- **Concurrent Chunked File Transfer:** Streams files in `4096-byte` chunks on a background thread (`FILE|<filename>|<size>|<raw_bytes>`) with progress updates (`20%` – `100%`), automatic `downloads/` directory creation, and automatic deletion of incomplete files if the connection drops mid-transfer.
- **Anti-Garble Console UI:** Uses `prompt_toolkit` (`patch_stdout`) and mutex locks (`state_lock`, `screen_lock`) so background network notifications never corrupt the line you are currently typing.

---

## Project Structure

```text
LAN-Chat-Socket/
├── lanchat.py          # Main entry point, CLI argument parser, and command dispatcher
├── state.py            # Thread-safe shared state and mutex locks (state_lock, screen_lock)
├── udp_discovery.py    # UDP Broadcaster (3s) & UDP Listener (port 9999, 10s timeout)
├── tcp_transport.py    # TCP Acceptor, per-peer Receiver threads, and stream framing
├── ui_and_file.py      # Console rendering, unread history manager, and TCP file transfer
├── downloads/          # Auto-created directory storing received files (ignored by Git)
└── README.md           # Project documentation and usage guide
```

### 6-Thread Concurrency Architecture
1. **Main Thread:** Reads user commands and chat messages via non-blocking `PromptSession`.
2. **UDP Broadcaster Thread:** Periodically announces presence (`HELLO|...`) every 3 seconds.
3. **UDP Listener Thread:** Listens on UDP port `9999`, updates peer timestamps, and purges timed-out peers.
4. **TCP Acceptor Thread:** Accepts incoming peer TCP connections and spawns dedicated receiver workers.
5. **TCP Receiver Threads:** One thread per connected peer to parse `MSG|`, `FILE|`, and `BYE|` frames.
6. **File Sender Thread:** Spawned per `/send` command to stream file chunks without freezing active chat.

---

## Installation & Setup

### 1. Prerequisites
- **Python 3.8+**
- **pip** (Python package manager)

### 2. Clone the Repository & Install Dependencies
```bash
git clone https://github.com/phamkhoa2007-maker/LAN-Chat-Socket.git
cd LAN-Chat-Socket
pip install prompt_toolkit
```

---

## How to Run

### Option A: Testing Multiple Peers on a Single Machine (Localhost)
Open multiple terminal windows in the project root directory and run each peer with a unique `--name` and `--port`:

```bash
# Terminal 1
python lanchat.py --name Khoa --port 5001

# Terminal 2
python lanchat.py --name Khang --port 5002

# Terminal 3
python lanchat.py --name Long --port 5003
```

### Option B: Testing Across Multiple Laptops on the Same LAN / Wi-Fi
Ensure all machines are connected to the same Wi-Fi subnet (with Client/AP Isolation disabled and firewall rules permitting UDP port `9999` and your chosen TCP port), then launch:

```bash
python lanchat.py --name <YourName> --port 5001
```

---

## Console Commands Reference

| Command | Description |
| :--- | :--- |
| `/list` | Display an aligned table of online peers, IP/port, status, and unread message counts. |
| `/chat <name\|number>` | Open a direct chat session with an online peer and replay saved/unread messages. |
| `/send <filepath>` | Send a local file to the active chat partner in a non-blocking background thread. |
| `/leave` | Close the current chat view and return to the main lobby. |
| `/status <online\|away>` | Change your presence status broadcast to the LAN. |
| `/help` | Display all available commands and syntax. |
| `/quit` | Send `BYE\|` to active connections, close all sockets, and exit cleanly. |

---

## Application-Layer Protocol Specification

- **UDP Presence Datagram:** `HELLO|<name>|<tcp_port>|<status>` (e.g., `HELLO|Khoa|5001|online`)
- **TCP Chat Message Frame:** `MSG|<utf8_byte_length>|<message_text>` (e.g., `MSG|11|Hello Long!`)
- **TCP File Transfer Frame:** `FILE|<filename>|<size_in_bytes>|<raw_binary_bytes>`
- **TCP Disconnect Frame:** `BYE|`

---

## Troubleshooting

- **Peers not appearing in `/list`:** Check that all devices are on the same subnet (`192.168.x.x`), Windows Defender Firewall allows Python through Private Networks, and UDP port `9999` is not blocked.
- **Address already in use (`WinError 10048`):** Ensure each instance on the same computer uses a distinct `--port` value (e.g., `5001`, `5002`, `5003`).