FROM python:3.12-slim
WORKDIR /app
COPY sim/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sim/*.py ./
COPY postgres/01_schema.sql ./schema.sql
CMD ["python", "-u", "main.py"]
