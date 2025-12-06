FROM python:3.9-slim

WORKDIR /app

COPY . .

# Install dependencies if any
RUN pip install --no-cache-dir -r requirements.txt

# Start the application
# Cross-reference: Trỏ tới file src/main.py
CMD ["python", "src/main.py"]
