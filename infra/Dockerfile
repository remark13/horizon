FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Модели кладутся в том, а не в слой образа: пересборка не должна
    # означать повторную выгрузку сотен мегабайт с Hugging Face.
    HF_HOME=/models

WORKDIR /app
COPY infra/constraints.docker-arm64-py312.txt /tmp/saia-constraints.txt

# hdbscan на arm64 колеса не публикует — только исходники, и они требуют
# компилятора. В slim-образе его нет. Ставим build-essential до pip и
# оставляем в образе: пересборка зависимостей не должна упираться в apt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# torch с индекса CPU-сборок. По умолчанию pip тянет вариант с CUDA —
# около двух гигабайт колёс nvidia-*, которые на Apple Silicon мертвы
# и на CPU-контейнере тоже. Отдельным слоем и до остальных зависимостей,
# чтобы sentence-transformers увидел torch уже установленным.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        -c /tmp/saia-constraints.txt torch==2.14.0+cpu

# Зависимости отдельным слоем, чтобы правка кода не пересобирала их заново.
COPY pyproject.toml ./
COPY saia/__init__.py saia/
RUN pip install --no-cache-dir -c /tmp/saia-constraints.txt -e ".[dev,db,web,bulk,ml]"

COPY . .
RUN python scripts/check_runtime_constraints.py

CMD ["python", "-m", "saia.methodology"]
