FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

# Install Requirements
WORKDIR /app

# Disable Python output buffering for real-time logs
ENV PYTHONUNBUFFERED=1

# Ensure the application source directory is discoverable by Python imports
ENV PYTHONPATH=/app/src

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Default Port 
EXPOSE 6969

# Run Uvicorn server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6969"]
