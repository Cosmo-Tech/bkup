FROM python:alpine

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY LICENSE.md /app
COPY pyproject.toml /app
COPY src /app/src
COPY templates /app/templates

RUN pip install --no-cache-dir .

CMD ["bkup", "--job"]