"""Build build/lambda.zip: services/ code plus Linux (Lambda) wheels of its dependencies.

Usage, from the repo root: python scripts/build_lambda.py

Dependencies are installed for manylinux x86_64 / CPython 3.12 so compiled packages such as
pydantic-core work on Lambda even when building on Windows or macOS. The zip is written with
forward-slash paths and explicit file permissions so Lambda's Linux runtime can import it.
"""

import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"
BUILD = ROOT / "build"
STAGING = BUILD / "lambda"
ZIP_PATH = BUILD / "lambda.zip"
PACKAGES = ("drysg", "handlers")


def main() -> None:
    shutil.rmtree(STAGING, ignore_errors=True)
    ZIP_PATH.unlink(missing_ok=True)
    STAGING.mkdir(parents=True)

    deps = tomllib.loads((SERVICES / "pyproject.toml").read_text())["project"]["dependencies"]
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--platform", "manylinux2014_x86_64",
            "--implementation", "cp",
            "--python-version", "3.12",
            "--only-binary=:all:",
            "--target", str(STAGING),
            "--quiet",
            *deps,
        ],
        check=True,
    )

    for package in PACKAGES:
        shutil.copytree(
            SERVICES / package,
            STAGING / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(STAGING.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            info = zipfile.ZipInfo(path.relative_to(STAGING).as_posix())
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())

    print(f"Wrote {ZIP_PATH} ({ZIP_PATH.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    main()
