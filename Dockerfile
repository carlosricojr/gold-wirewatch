FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN pip install uv && uv sync --frozen

EXPOSE 8787
CMD ["uv", "run", "python", "wirewatch.py", "run"]
