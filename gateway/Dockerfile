FROM python:3.12-slim
WORKDIR /app
COPY gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# même calcul d'indicateurs que la simulation : une seule source de vérité
COPY sim/indicators.py gateway/main.py ./
CMD ["python", "-u", "main.py"]
