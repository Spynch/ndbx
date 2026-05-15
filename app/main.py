import os
from fastapi import FastAPI
import uvicorn

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


def main() -> None:
    port_str = os.getenv("APP_PORT", "8080")
    try:
        port = int(port_str)
    except ValueError:
        raise ValueError(f"APP_PORT must be an integer, got: {port_str}")

    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()