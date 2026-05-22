FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PATH=/home/optimizer/.local/bin:$PATH

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ghostscript=10.0.0~dfsg-11+deb12u8 \
        poppler-utils=22.12.0-2+deb12u1 \
        util-linux=2.38.1-5+deb12u3 \
        curl=7.88.1-10+deb12u14 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash optimizer \
    && mkdir -p /data/kdv_optimize/input /data/kdv_optimize/output \
    && chown -R optimizer:optimizer /data/kdv_optimize

USER optimizer
WORKDIR /app

COPY --chown=optimizer:optimizer requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=optimizer:optimizer . .

EXPOSE 5001
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5001", "--timeout", "150", "optimizer_app:app"]
