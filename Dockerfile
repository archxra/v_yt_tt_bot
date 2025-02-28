# Use an official lightweight Python image.
FROM python:3.10-slim

# Set the working directory in the container.
WORKDIR /app

# Copy the requirements file into the container.
COPY requirements.txt .

# Install any needed packages specified in requirements.txt.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container.
COPY . .

# Expose port 8080 if your app listens on that port (optional).
EXPOSE 8080

# Define environment variable, if needed (optional).
ENV PYTHONUNBUFFERED=1

# Run the application.
CMD ["python", "main.py"]
