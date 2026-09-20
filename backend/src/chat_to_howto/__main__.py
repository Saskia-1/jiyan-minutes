"""Run the production loopback server without a reload or proxy trust."""

from __future__ import annotations

import uvicorn

from chat_to_howto.runtime import create_runtime_app


def main() -> None:
    uvicorn.run(
        create_runtime_app(),
        host="127.0.0.1",
        port=8765,
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
