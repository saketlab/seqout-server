## Setup 

Create a virtual environment and download all dependencies:

```bash
uv venv
source .venv/bin/activate
uv sync
```

To run the production server:

```bash
fastapi run main.py --host 0.0.0.0 --port 8000
```