# SLA Compliance Monitoring - Documentation

## Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation & Deployment](#installation--deployment)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Usage Examples](#usage-examples)
- [Supported Service Types](#supported-service-types)
- [Troubleshooting](#troubleshooting)
- [Security Considerations](#security-considerations)

---

## Overview

The SLA Compliance Monitoring application is a cloud-native solution that monitors and reports on Service Level Agreement (SLA) compliance for Google Cloud Platform services. It calculates uptime percentages based on request success/failure metrics and provides a simple dashboard for viewing compliance reports.

### Key Capabilities
- **Multi-project monitoring**: Track SLA compliance across multiple GCP projects
- **Concurrent processing**: Efficient parallel metric collection using configurable worker pools
- **Persistent storage**: Historical reports stored in Firestore
- **Real-time dashboard**: Web-based UI for report generation and viewing
- **RESTful API**: Programmatic access for automation and integration

---

## Architecture

### System Flow

```
┌─────────────────┐
│  Cloud Scheduler│
│  (Optional)     │
└────────┬────────┘
         │ HTTP Trigger
         ▼
┌─────────────────────────────────┐
│   Cloud Run / GKE               │
│  ┌──────────────────────────┐   │
│  │  FastAPI Application     │   │
│  │  - API Endpoints         │   │
│  │  - Background Tasks      │   │
│  │  - Dashboard UI          │   │
│  └──────┬───────────────────┘   │
└─────────┼───────────────────────┘
          │
          ├─────────────────┐
          │                 │
          ▼                 ▼
┌──────────────────┐  ┌──────────────┐
│ Cloud Monitoring │  │  Firestore   │
│  - Metrics API   │  │  - Reports   │
└──────────────────┘  └──────────────┘
```

### Components

1. **Application Container** (Cloud Run/GKE)
   - FastAPI web service
   - Background task processing
   - HTML dashboard interface

2. **Cloud Monitoring API**
   - Source of truth for service metrics
   - Provides request counts and error rates

3. **Firestore Database**
   - Stores compliance reports
   - Job status tracking
   - Historical data retention

4. **Cloud Scheduler** (Optional)
   - Automated report generation
   - Scheduled compliance checks

---

## Features

### SLA Calculation
- **1-minute granularity**: Metrics aligned to 60-second intervals
- **Error rate analysis**: Identifies minutes where error rate ≥ 100%
- **Uptime percentage**: `((total_minutes - downtime_minutes) / total_minutes) × 100`
- **Compliance checking**: Automatic comparison against defined thresholds

### Monitoring Coverage
- **Cloud Run**: Request count and 5xx error tracking
- **Cloud Storage (GCS)**: API request monitoring with 500 error detection
- **BigQuery**: Query success/failure tracking

### Concurrency & Performance
- Thread pool executor for parallel metric fetching
- Configurable worker count (default: 10)
- Optimized for multi-service, multi-project scenarios

---

## Prerequisites

### GCP Requirements
1. **Google Cloud Project** with the following APIs enabled:
   - Cloud Monitoring API
   - Firestore API
   - Cloud Run API (if deploying to Cloud Run)
   - Secret Manager API (for CI/CD)

2. **IAM Permissions**:
   ```
   roles/monitoring.viewer          # Read metrics
   roles/datastore.user            # Firestore read/write
   roles/run.invoker               # Cloud Run execution (if applicable)
   ```

3. **Firestore Database**:
   - Create a Firestore database in Native mode
   - Collection `sla_reports` will be created automatically

### Local Development
- Python 3.11+
- Docker (for containerization)
- gcloud CLI (for deployment)

---

## Installation & Deployment

### Local Development Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd sla-compliance-monitoring
   ```

2. **Create virtual environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up authentication**:
   ```bash
   gcloud auth application-default login
   ```

5. **Run locally**:
   ```bash
   export PORT=8080
   uvicorn main:app --host 0.0.0.0 --port 8080 --reload
   ```

6. **Access dashboard**:
   ```
   http://localhost:8080
   ```

### Docker Build

```bash
# Build image
docker build -t sla-compliance-monitor:latest .

# Run container
docker run -p 8080:8080 \
  -e PORT=8080 \
  -e GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json \
  -v /path/to/key.json:/path/to/key.json \
  sla-compliance-monitor:latest
```

### Cloud Run Deployment

1. **Build and push to Docker Hub** (using Cloud Build):
   ```bash
   gcloud builds submit --config=cloudbuild.yml
   ```

2. **Deploy to Cloud Run**:
   ```bash
   gcloud run deploy sla-compliance-monitor \
     --image docker.io/raghuporumamilla/rporumamilla:latest \
     --platform managed \
     --region us-central1 \
     --allow-unauthenticated \
     --memory 512Mi \
     --timeout 540s
   ```

3. **Note the service URL** provided after deployment

### GKE Deployment

1. **Create deployment YAML** (`k8s-deployment.yaml`):
   ```yaml
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: sla-monitor
   spec:
     replicas: 2
     selector:
       matchLabels:
         app: sla-monitor
     template:
       metadata:
         labels:
           app: sla-monitor
       spec:
         containers:
         - name: sla-monitor
           image: raghuporumamilla/rporumamilla:latest
           ports:
           - containerPort: 8080
           env:
           - name: PORT
             value: "8080"
   ---
   apiVersion: v1
   kind: Service
   metadata:
     name: sla-monitor-service
   spec:
     type: LoadBalancer
     selector:
       app: sla-monitor
     ports:
     - port: 80
       targetPort: 8080
   ```

2. **Apply configuration**:
   ```bash
   kubectl apply -f k8s-deployment.yaml
   ```

---

## Configuration

### Service Configuration Schema

```json
{
  "projects": [
    {
      "id": "your-gcp-project-id",
      "services": [
        {
          "name": "service-name",
          "type": "cloud_run_revision|gcs_bucket|bigquery_project",
          "threshold": 99.9
        }
      ]
    }
  ],
  "days": 30,
  "max_workers": 10
}
```

### Configuration Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `projects` | Array | Yes | - | List of GCP projects to monitor |
| `projects[].id` | String | Yes | - | GCP project ID |
| `projects[].services` | Array | Yes | - | Services within the project |
| `services[].name` | String | Yes | - | Service identifier (varies by type) |
| `services[].type` | String | Yes | - | Service type (see supported types) |
| `services[].threshold` | Float | Yes | - | SLA threshold (e.g., 99.9 for 99.9%) |
| `days` | Integer | No | 30 | Reporting period in days |
| `max_workers` | Integer | No | 10 | Thread pool size for concurrent fetching |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | Yes | HTTP server port (set by Cloud Run automatically) |
| `GOOGLE_APPLICATION_CREDENTIALS` | No | Path to service account key (for local dev) |

---

## API Reference

### POST `/v1/compliance_report`

Creates a new SLA compliance report job.

**Request Body**:
```json
{
  "projects": [
    {
      "id": "my-gcp-project",
      "services": [
        {
          "name": "my-cloud-run-service",
          "type": "cloud_run_revision",
          "threshold": 99.9
        }
      ]
    }
  ],
  "days": 7,
  "max_workers": 5
}
```

**Response** (202 Accepted):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Errors**:
- `400`: Invalid request body
- `500`: Internal server error

---

### GET `/v1/compliance_report`

Retrieves the 10 most recent compliance reports.

**Response** (200 OK):
```json
[
  {
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "completed",
    "started_at": "2026-02-13T10:30:00",
    "finished_at": "2026-02-13T10:32:15",
    "days": 7,
    "data": [
      {
        "project_id": "my-gcp-project",
        "metrics": [
          {
            "service": "my-cloud-run-service",
            "uptime_pct": 99.95,
            "downtime_minutes": 5,
            "compliant": true
          }
        ]
      }
    ]
  }
]
```

---

### GET `/v1/compliance_report/{job_id}`

Retrieves a specific compliance report by job ID.

**Parameters**:
- `job_id` (path): UUID of the report job

**Response** (200 OK):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "started_at": "2026-02-13T10:30:00",
  "finished_at": "2026-02-13T10:32:15",
  "days": 7,
  "data": [...]
}
```

**Errors**:
- `404`: Report not found

**Status Values**:
- `processing`: Report generation in progress
- `completed`: Report successfully generated
- `failed`: Error occurred during generation

---

### GET `/`

Serves the interactive HTML dashboard.

**Features**:
- Run new reports via web form
- View recent reports with auto-refresh (10s interval)
- Visual compliance indicators (✅/❌)
- Real-time status updates

---

## Usage Examples

### Example 1: Cloud Run Service Monitoring

```json
{
  "projects": [
    {
      "id": "production-project-123",
      "services": [
        {
          "name": "payment-api",
          "type": "cloud_run_revision",
          "threshold": 99.95
        },
        {
          "name": "user-service",
          "type": "cloud_run_revision",
          "threshold": 99.9
        }
      ]
    }
  ],
  "days": 30
}
```

### Example 2: Multi-Service, Multi-Project

```json
{
  "projects": [
    {
      "id": "prod-project",
      "services": [
        {
          "name": "api-service",
          "type": "cloud_run_revision",
          "threshold": 99.9
        },
        {
          "name": "data-warehouse",
          "type": "bigquery_project",
          "threshold": 99.5
        }
      ]
    },
    {
      "id": "storage-project",
      "services": [
        {
          "name": "user-uploads",
          "type": "gcs_bucket",
          "threshold": 99.95
        }
      ]
    }
  ],
  "days": 7,
  "max_workers": 15
}
```

### Example 3: cURL API Call

```bash
curl -X POST https://your-service-url.run.app/v1/compliance_report \
  -H "Content-Type: application/json" \
  -d '{
    "projects": [
      {
        "id": "my-project",
        "services": [
          {
            "name": "my-service",
            "type": "cloud_run_revision",
            "threshold": 99.9
          }
        ]
      }
    ],
    "days": 7
  }'
```

### Example 4: Python Script

```python
import requests
import json

url = "https://your-service-url.run.app/v1/compliance_report"
config = {
    "projects": [
        {
            "id": "my-project-id",
            "services": [
                {
                    "name": "service-name",
                    "type": "cloud_run_revision",
                    "threshold": 99.9
                }
            ]
        }
    ],
    "days": 30
}

response = requests.post(url, json=config)
job_id = response.json()["job_id"]
print(f"Report started: {job_id}")

# Poll for completion
import time
while True:
    result = requests.get(f"{url}/{job_id}").json()
    if result["status"] == "completed":
        print(json.dumps(result, indent=2))
        break
    time.sleep(5)
```

---

## Supported Service Types

### 1. Cloud Run (`cloud_run_revision`)

**Service Name**: Cloud Run service name (e.g., `my-api-service`)

**Metrics Used**:
- Total requests: `run.googleapis.com/request_count`
- Errors: Requests with `response_code_class="5xx"`

**SLA Logic**: A minute is considered "down" if 100% of requests in that minute returned 5xx errors.

**Example**:
```json
{
  "name": "payment-api",
  "type": "cloud_run_revision",
  "threshold": 99.95
}
```

---

### 2. Cloud Storage (`gcs_bucket`)

**Service Name**: GCS bucket name (e.g., `my-data-bucket`)

**Metrics Used**:
- Total requests: `storage.googleapis.com/api/request_count`
- Errors: Requests with `response_code="500"`

**SLA Logic**: A minute is considered "down" if 100% of API requests in that minute returned 500 errors.

**Example**:
```json
{
  "name": "production-uploads",
  "type": "gcs_bucket",
  "threshold": 99.9
}
```

---

### 3. BigQuery (`bigquery_project`)

**Service Name**: Not used (monitors entire project)

**Metrics Used**:
- Total queries: `bigquery.googleapis.com/query/count`
- Successful queries: Queries with `state="SUCCEEDED"`

**SLA Logic**: A minute is considered "down" if 100% of queries in that minute failed.

**Example**:
```json
{
  "name": "any-identifier",
  "type": "bigquery_project",
  "threshold": 99.5
}
```

**Note**: For BigQuery, the `name` field is not used in metric filtering but should still be provided for identification purposes.

---

## Troubleshooting

### Common Issues

#### 1. "No data returned" or 0% uptime

**Causes**:
- Service name mismatch
- Metrics not available for the time period
- Insufficient permissions

**Solutions**:
- Verify service name exactly matches GCP resource
- Check that the service had traffic during the period
- Confirm monitoring.viewer role is granted
- Test with Cloud Monitoring UI to verify metric existence

#### 2. Report stuck in "processing" status

**Causes**:
- Background task failure
- Firestore write permissions issue
- API rate limiting

**Solutions**:
- Check application logs: `gcloud run logs read --service sla-compliance-monitor`
- Verify Firestore permissions
- Reduce `max_workers` if hitting rate limits
- Check for exceptions in background task

#### 3. "404 Not Found" when fetching metrics

**Causes**:
- Incorrect project ID
- Monitoring API not enabled
- Service account lacks permissions

**Solutions**:
- Double-check project ID matches GCP console
- Enable Cloud Monitoring API: `gcloud services enable monitoring.googleapis.com`
- Grant monitoring.viewer role to service account

#### 4. Timeout errors on large reports

**Causes**:
- Too many services
- Long time periods (90+ days)
- Low worker count

**Solutions**:
- Increase Cloud Run timeout: `--timeout 900s`
- Increase `max_workers` to 20-30 for large jobs
- Break into smaller time periods
- Consider caching or incremental reports

---

## Security Considerations

### Authentication & Authorization

1. **API Authentication**:
   - Cloud Run: Use Cloud IAM for authentication
   - Add `--no-allow-unauthenticated` flag for secure deployments
   - Use service accounts with minimal permissions

2. **Principle of Least Privilege**:
   ```bash
   # Create dedicated service account
   gcloud iam service-accounts create sla-monitor-sa
   
   # Grant only necessary roles
   gcloud projects add-iam-policy-binding PROJECT_ID \
     --member="serviceAccount:sla-monitor-sa@PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/monitoring.viewer"
   
   gcloud projects add-iam-policy-binding PROJECT_ID \
     --member="serviceAccount:sla-monitor-sa@PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/datastore.user"
   ```

### Data Protection

1. **Firestore Security Rules**:
   ```javascript
   rules_version = '2';
   service cloud.firestore {
     match /databases/{database}/documents {
       match /sla_reports/{document} {
         allow read: if request.auth != null;
         allow write: if request.auth != null;
       }
     }
   }
   ```

2. **Network Security**:
   - Use VPC Service Controls for sensitive projects
   - Enable Cloud Armor for DDoS protection
   - Implement IP allowlisting if needed

### Secret Management

- Store Docker Hub credentials in Secret Manager (as shown in `cloudbuild.yml`)
- Never commit credentials to version control
- Rotate secrets regularly

### Audit Logging

Enable Cloud Audit Logs for monitoring access:
```bash
gcloud projects update PROJECT_ID --update-labels=compliance=sla-monitoring
```

---

## Performance Optimization

### Tuning for Large Scale

1. **Adjust worker count** based on service count:
   - 1-10 services: `max_workers: 5`
   - 11-50 services: `max_workers: 15`
   - 50+ services: `max_workers: 30`

2. **Memory allocation**:
   - Small jobs (<10 services): 512Mi
   - Medium jobs (10-50 services): 1Gi
   - Large jobs (50+ services): 2Gi

3. **Caching strategies**:
   - Implement Redis for frequently accessed reports
   - Use Cloud CDN for dashboard assets
   - Cache Firestore reads for recent reports

---

## Maintenance & Monitoring

### Application Monitoring

1. **Cloud Logging queries**:
   ```
   resource.type="cloud_run_revision"
   resource.labels.service_name="sla-compliance-monitor"
   severity>=ERROR
   ```

2. **Key metrics to track**:
   - Request latency (p50, p95, p99)
   - Error rate
   - Background task completion time
   - Firestore read/write operations

### Database Maintenance

1. **Cleanup old reports** (Cloud Function example):
   ```python
   from google.cloud import firestore
   from datetime import datetime, timedelta
   
   def cleanup_old_reports(request):
       db = firestore.Client()
       cutoff = datetime.now() - timedelta(days=90)
       
       docs = db.collection('sla_reports')\
                .where('started_at', '<', cutoff.isoformat())\
                .stream()
       
       for doc in docs:
           doc.reference.delete()
   ```

2. **Index optimization**:
   - Create composite index on `started_at` (descending) for faster queries
   - Monitor index usage in Firestore console

---

## Support & Contribution

### Getting Help
- Check logs: `gcloud run logs read`
- Review Firestore documents for job errors
- Consult GCP documentation for metric specifications

### Future Enhancements
- Email/Slack notification integration
- Custom metric support
- Multi-region aggregation
- Scheduled report subscriptions
- Export to BigQuery for advanced analytics

---

## License

*Add your license information here*

## Changelog

### v1.0.0 (Current)
- Initial release
- Support for Cloud Run, GCS, BigQuery
- Web dashboard
- RESTful API
- Background job processing
