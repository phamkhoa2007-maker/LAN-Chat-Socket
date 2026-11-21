# Các đoạn code vàng cần lấy từ udp_broadcast_demo.py:
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # Cho phép nhiều app chung cổng 9999
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1) # Cho phép phát loa toàn mạng LAN
s.bind(("", 9999))                                      # Lắng nghe trên mọi card mạng

# Khi phát sóng (3 giây 1 lần):
msg = f"HELLO|{name}|{tcp_port}|{status}".encode("utf-8")
s.sendto(msg, (bcast_ip, 9999))

# Khi nhận sóng:
data, addr = s.recvfrom(1024) # addr[0] chính là địa chỉ IP của người gửi!
parts = data.decode("utf-8", errors="replace").split("|")
# Nếu parts[1] == tên_của_mình thì bỏ qua (continue) vì tự nghe lại tiếng loa của chính mình.
