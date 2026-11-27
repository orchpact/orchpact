FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.11.7
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/orchpact/venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY examples/fulfillment/input.json examples/fulfillment/workflow.json ./examples/fulfillment/
COPY examples/mystery-story/input.json examples/mystery-story/workflow.json ./examples/mystery-story/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN groupadd --gid 10001 orchpact && useradd --uid 10001 --gid 10001 --home-dir /var/lib/orchpact/var/crewai-runtime --no-create-home orchpact
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl --fail --silent --show-error https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && printf '%s\n' 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/orchpact/venv /opt/orchpact/venv
COPY docker/entrypoint.py /opt/orchpact/entrypoint.py
ENV PATH="/opt/orchpact/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN install -d -m 700 -o orchpact -g orchpact /var/lib/orchpact
USER 10001:10001
WORKDIR /var/lib/orchpact
EXPOSE 8780
ENTRYPOINT ["python", "/opt/orchpact/entrypoint.py"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8780"]
