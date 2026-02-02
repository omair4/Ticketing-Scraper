# AdmitONE scraper - AWS Lambda container image
# One event per invocation; run Lambda concurrently for multiple events.

FROM public.ecr.aws/lambda/python:3.11

# Install system dependencies required by Chromium (Playwright)
RUN yum install -y \
    atk \
    cups-libs \
    gtk3 \
    libXcomposite \
    libXdamage \
    libXrandr \
    mesa-libgbm \
    nss \
    && yum clean all

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser
RUN playwright install chromium

# Copy application code
COPY config.py .
COPY browser.py .
COPY main.py .
COPY lambda_handler.py .
COPY models.py .
COPY utils ./utils
COPY scrapers ./scrapers

# Set Lambda handler
CMD ["lambda_handler.lambda_handler"]
