FROM python:3.13-slim

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    duckdb==1.5.2 \
    redis==7.4.0 \
    python-dotenv==1.2.2

WORKDIR /app

COPY redis_data_receiver1.3.py .
COPY writer_service_optimized.py .
COPY snapshot_sender.py .
COPY start_all.sh .

# 数据目录
RUN mkdir -p /app/data

CMD ["python", "-u", "writer_service_optimized.py"]
