# Android: Netrunner

Local Android: Netrunner API and terminal client. Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are required.

The API is the runnable app at this stage. The React client is not implemented yet.

## Run the app (API)

From the repository root:

```bash
uv run uvicorn api.app:app --reload
```

The API listens at <http://127.0.0.1:8000>. Check that it is running at <http://127.0.0.1:8000/health>; interactive API docs are at <http://127.0.0.1:8000/docs>.

See the [API guide](docs/api/api.md) for endpoint usage, authentication, commands, and WebSocket updates. The OpenAPI snapshot can be regenerated with `python docs/api/generate_openapi.py`.

## Run the CLI

Keep the API running. Open a second terminal at the repository root and run:

```bash
uv run --project clients/cli anr
```

`uv run` installs/synchronizes each project's dependencies automatically. The CLI connects to `http://127.0.0.1:8000` by default. To use another API address, set `ANR_API_URL` before starting it:

PowerShell:

```powershell
$env:ANR_API_URL = "http://127.0.0.1:8000"
uv run --project clients/cli anr
```

macOS/Linux:

```bash
export ANR_API_URL="http://127.0.0.1:8000"
uv run --project clients/cli anr
```

The current CLI can start a game against a scripted opponent using a preconstructed deck or an imported NetrunnerDB deck file. Host Game and Join Game are not implemented yet. Game sessions are kept in memory and are cleared when the backend stops.

## Lint

From the repository root, check the API, shared content, CLI, and tests:

```bash
uv run ruff check .
```

Apply safe automatic fixes with `uv run ruff check . --fix`.
