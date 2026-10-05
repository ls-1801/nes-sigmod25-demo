FROM python:3.11-slim AS runtime

WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir paho-mqtt PyYAML \
 && python -m pip cache purge

COPY datagen.py config.yml *.csv ./

CMD ["python", "datagen.py"]
