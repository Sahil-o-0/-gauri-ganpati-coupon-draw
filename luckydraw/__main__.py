import socket

import uvicorn

from . import config


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "this-pc-ip"


if __name__ == "__main__":
    print("=" * 60)
    print(" Gauri Ganpati Lucky Draw")
    print(f" On this PC, open:      http://localhost:{config.PORT}")
    print(f" On other shop PCs:     http://{lan_ip()}:{config.PORT}")
    print(" Keep this window open. Close it to stop the software.")
    print("=" * 60)
    uvicorn.run("luckydraw.main:app", host=config.HOST, port=config.PORT, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="*")
