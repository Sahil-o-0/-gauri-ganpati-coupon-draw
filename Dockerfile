FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY luckydraw luckydraw
COPY connector connector
EXPOSE 8080
CMD ["python", "-m", "luckydraw"]
