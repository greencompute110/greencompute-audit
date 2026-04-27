FROM python:3.12-slim

WORKDIR /app

# Install deps first (cache layer) — copy only files that affect resolution.
COPY pyproject.toml ./

# pyproject is the source of truth; pin the deps it declares.
RUN pip install --no-cache-dir \
    "httpx>=0.27" \
    "substrate-interface>=1.7" \
    "pydantic>=2.7" \
    "click>=8.1"

# Now copy the package source. Editable install picks up local code so
# `git pull` + container restart applies updates without rebuild.
COPY . .
RUN pip install --no-cache-dir -e .

ENTRYPOINT ["python", "-m", "audit"]
CMD ["--loop"]
