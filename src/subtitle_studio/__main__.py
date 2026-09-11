import uvicorn


def main() -> None:
    uvicorn.run(
        "subtitle_studio.api:app",
        host="0.0.0.0",
        port=8000,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
