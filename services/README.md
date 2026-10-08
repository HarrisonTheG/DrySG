# Backend development

AWS Lambda handlers built with FastAPI and Python. Contains the core backend business logic.

Use Python 3.12 or newer. From `services/`, create an isolated environment:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check drysg handlers tests
```

On macOS/Linux use `.venv/bin/python` instead of `.venv/Scripts/python.exe`.
The `.venv` directory is ignored by Git. Tests use recorded upstream fixtures,
HTTP mock transports and Moto DynamoDB; no AWS account or OneMap token is needed.

Stream A provides weather ingestion, location lookup and weather/cache repositories.
See [the data handoff](../docs/contracts/data.md) for usage, failure behavior and
remaining API/alert integration work. Existing scoring stays in `drysg/core/scoring.py`.
Terraform owns table provisioning; the repositories never create real tables.
