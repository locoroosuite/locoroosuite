from __future__ import annotations

import uvicorn


def main():
    app = create_asgi_app_from_module()
    uvicorn.run(app, host="0.0.0.0", port=8001)


def create_asgi_app_from_module():
    from app.mcp import create_asgi_app
    return create_asgi_app()


if __name__ == "__main__":
    main()
