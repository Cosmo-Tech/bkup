# FROM python:alpine
FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY *.md /app
COPY pyproject.toml /app
COPY src /app/src
COPY templates /app/templates


# RUN apk add --no-cache --virtual .build-deps gcc musl-dev python3-dev libffi-dev \
#     && pip install --no-cache-dir . \
#     && apk del .build-deps

RUN pip install --no-cache-dir .


# CMD ["bkup", "--job"]
# CMD ["bkup"]
CMD ["bkup", "export"]
