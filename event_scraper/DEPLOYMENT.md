# AdmitONE Scraper – Lambda & Docker Instructions

This document describes how to run the inventory scraper as an AWS Lambda function in a Docker image. **Each Lambda invocation scrapes one event**; invoke the function concurrently to scrape many events in parallel.

---

## Overview

| Item | Description |
|------|-------------|
| **Runtime** | Python 3.11 in a container image |
| **Invocation** | One event per invocation (concurrent invocations supported) |
| **Input** | `eventId`, `event_name`, `venue_name`, `event_datetime`, `ticket_url`; optional `proxy` |
| **Output** | JSON body with full scrape result (event, seats/ticket categories, prices, etc.) |

---

## Lambda Event Payload

Each invocation must send a JSON payload with these fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `eventId` | string | Yes | Event identifier (e.g. slug or ID) |
| `event_name` | string | Yes | Event display name |
| `venue_name` | string | Yes | Venue name |
| `event_datetime` | string | No | Event date/time (e.g. `"Sat. Feb 14, 2026 at 7:00pm"`) |
| `ticket_url` | string | Yes | Full AdmitONE event page URL to scrape |
| `proxy` | string | No | Proxy URL, e.g. `http://host:port` or `http://user:pass@host:port` |

**Example payload:**

```json
{
  "eventId": "681d11117d3a4df5fd2d2c24",
  "event_name": "Andrew Callaghan: All Gas No Brakes",
  "venue_name": "Queen Elizabeth Theatre",
  "event_datetime": "Sat. Feb 14, 2026 at 7:00pm",
  "ticket_url": "https://admitone.com/events/toronto/pro/other/queen-elizabeth-theatre/andrew-callaghan/andrew-callaghan/681d11117d3a4df5fd2d2c24",
  "proxy": "http://proxy.example.com:8080"
}
```

- **Proxy** is optional. If omitted, the function uses the `PROXY` environment variable (if set). Leave `proxy` out and `PROXY` empty to run without a proxy.

---

## Build the Docker Image

From the project root:

```bash
cd admitone_scraper
docker build -t admitone-scraper:latest .
```

The image includes Python dependencies, Playwright, and Chromium. The first build may take several minutes.

---

## Run Locally with Docker

To test the handler logic locally (without Lambda RIE):

```bash
# Build the image
docker build -t admitone-scraper:latest .

# Run the Lambda handler via Python (one-off test)
docker run --rm -e AWS_LAMBDA_FUNCTION_HANDLER=lambda_handler.lambda_handler \
  admitone-scraper:latest \
  python -c "
import json
from lambda_handler import lambda_handler
event = {
    \"eventId\": \"test-1\",
    \"event_name\": \"Test Event\",
    \"venue_name\": \"Test Venue\",
    \"event_datetime\": \"Sat. Feb 14, 2026 at 7:00pm\",
    \"ticket_url\": \"https://admitone.com/events/...\"
}
result = lambda_handler(event, None)
print(json.dumps(result, indent=2))
"
```

For a full local Lambda simulation (with Runtime Interface Emulator), use SAM:

```bash
# Invoke the function locally (requires AWS SAM CLI)
sam local invoke AdmitOneScraperFunction --event event.json
```

Create `event.json` with the payload from the example above.

---

## Deploy to AWS Lambda (SAM)

### Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) configured with credentials
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Docker (for building the image)

### Build

From the project root:

```bash
sam build
```

This builds the container image using the Dockerfile and prepares the deployment artifacts.

### Deploy

First-time deploy (interactive):

```bash
sam deploy --guided
```

You will be prompted for:

- **Stack name** (e.g. `admitone-scraper`)
- **AWS Region**
- **ProxyUrl** (optional; leave blank for no default proxy)
- Confirm changes before deploy, allow SAM CLI IAM role creation, etc.

Subsequent deploys:

```bash
sam deploy
```

### Optional: Set default proxy at deploy time

In `template.yaml`, the `ProxyUrl` parameter defaults to `""`. To set a default proxy for all invocations:

- When running `sam deploy --guided`, enter your proxy URL when prompted for `ProxyUrl`, or
- Deploy with overrides:  
  `sam deploy --parameter-overrides ProxyUrl=http://your-proxy:port`

The Lambda environment variable `PROXY` is set from `ProxyUrl`. Per-invocation `event.proxy` overrides this when present.

---

## Invoke the Lambda Function

### Single invocation (AWS CLI)

```bash
aws lambda invoke \
  --function-name <YourFunctionName> \
  --payload file://event.json \
  --cli-binary-format raw-in-base64-out \
  response.json
cat response.json
```

Use the logical function name from your stack (e.g. `admitone-scraper-AdmitOneScraperFunction-...`) or the physical name from the SAM outputs.

### Concurrent invocations (multiple events)

Invoke the same function multiple times in parallel (e.g. from a script or Step Functions). Each invocation should use a different `event.json` (or payload) with a different `ticket_url` (and optionally different `eventId`, `event_name`, etc.). Example with shell:

```bash
# event1.json, event2.json, ... each with one event
for f in event1.json event2.json event3.json; do
  aws lambda invoke --function-name <YourFunctionName> \
    --payload file://"$f" --cli-binary-format raw-in-base64-out \
    "response_$f" &
done
wait
```

Or use the AWS SDK (boto3) or Step Functions Map state to run many events concurrently.

---

## Response Format

- **Success (200):**  
  `body` is a JSON string of the full scrape result (event info, `seats` or `ticket_categories`, prices, `scraped_at`, etc.).

- **Client error (400):**  
  Missing `ticket_url`.  
  `body`: `{"error": "ticket_url is required"}`

- **Server error (500):**  
  Scraping or runtime failure.  
  `body`: `{"error": "<message>"}`

Parse `response.json` and, if `statusCode` is 200, parse `body` as JSON to get the scrape result.

---

## Configuration Summary

| Setting | Where | Description |
|--------|--------|-------------|
| **Memory** | `template.yaml` | 3008 MB (suitable for Chromium) |
| **Timeout** | `template.yaml` | 900 s (15 min) per invocation |
| **Proxy (default)** | `template.yaml` → `ProxyUrl` / env `PROXY` | Optional default proxy |
| **Proxy (per request)** | Event payload `proxy` | Overrides default when provided |

---

## Troubleshooting

1. **Lambda times out**  
   Increase `Timeout` in `template.yaml` (max 900 s for Lambda) or optimize the event page (e.g. faster-loading URLs).

2. **Out of memory**  
   Increase `MemorySize` in `template.yaml` (e.g. 3008 MB is already high; ensure no memory leak in custom code).

3. **Chromium/Playwright errors in Lambda**  
   The Dockerfile installs Chromium and system dependencies for the Lambda base image. If you use a different base image, ensure all [Playwright dependencies for Chromium](https://playwright.dev/python/docs/docker) are installed.

4. **Proxy not used**  
   Confirm `event.proxy` or the `PROXY` environment variable is set. Format: `http://host:port` or `http://user:pass@host:port`.

5. **Build failures (SAM/Docker)**  
   Run `docker build -t admitone-scraper:latest .` directly to see Docker errors. Ensure Docker has enough disk space and that the Dockerfile and `template.yaml` point to the correct Dockerfile path.
