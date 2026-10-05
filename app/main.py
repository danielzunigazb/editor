"""Run the web application:  python -m app.main   (settings from MLT_APP_* environment variables, see app/config.py)."""
import uvicorn

from .api import create_app
from .config import AppSettings


def main():
    s = AppSettings.from_env()
    if s.token_generated:
        print(f"MLT_APP_TOKEN was not set: this run's access token is  {s.token}", flush=True)
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
