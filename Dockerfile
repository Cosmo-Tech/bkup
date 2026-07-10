FROM python:alpine

WORKDIR /app
COPY LICENSE.md /app
COPY requirements.txt /app
COPY src /app/src

RUN pip install -r requirements.txt

CMD ["python", "-m", "src.master", "--job"]