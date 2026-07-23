FROM python:alpine

WORKDIR /app
COPY LICENSE.md /app
COPY pyproject.toml /app
COPY src /app/src

RUN pip install --no-cache-dir .

CMD ["python", "-m", "src.master", "--job"]