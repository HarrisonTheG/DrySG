"""API Lambda: API Gateway HTTP API (payload v2) -> FastAPI via Mangum."""

import logging
import os

from mangum import Mangum

from drysg.api.app import create_app
from drysg.api.deps import build_deps

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_origin = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
# Swagger UI is for local use; the deployed API does not publish /docs or /openapi.json.
app = create_app(build_deps, cors_origins=[_origin] if _origin else None, enable_docs=False)
handler = Mangum(app, lifespan="off")
