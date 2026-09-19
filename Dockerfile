FROM python:3.11-slim

WORKDIR /app

# Copy the app source code
COPY . /app/

# Expose the application port
EXPOSE 8107

# Set environment variables for Docker
ENV WECHAT_COLLECTOR_HOST=0.0.0.0
ENV WECHAT_COLLECTOR_PORT=8107
ENV WECHAT_COLLECTOR_DB=/app/data/wechat-oa.db

# Run the app
CMD ["python", "app.py"]
