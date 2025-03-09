# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Install git and any other needed packages
RUN apt-get update && apt-get install -y git ffmpeg && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy requirements.txt and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code to the container
COPY . .

# Expose port 8080 if needed
EXPOSE 10000

# Run the application
CMD ["python", "main.py"]
