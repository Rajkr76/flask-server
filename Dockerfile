# Use official Python image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set working directory
WORKDIR /app

# Copy app files
COPY . /app

# Install dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Expose port for Render
EXPOSE 10000

# Command to run the app with Gunicorn for production
CMD ["gunicorn", "--timeout", "120", "-b", "0.0.0.0:10000", "app:app"]


