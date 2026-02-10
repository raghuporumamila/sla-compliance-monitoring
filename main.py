import time
import uuid
import concurrent.futures
from typing import List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from google.api_core import exceptions
from google.cloud import monitoring_v3, firestore
from datetime import datetime

app = FastAPI(title="SLA Dashboard")
monitoring_client = monitoring_v3.MetricServiceClient()
db = firestore.Client()
COLLECTION_NAME = "sla_reports"


# --- Pydantic Models ---

class ServiceConfig(BaseModel):
    name: str
    type: str
    threshold: float


class ProjectConfig(BaseModel):
    id: str
    services: List[ServiceConfig]


class ReportRequest(BaseModel):
    projects: List[ProjectConfig]
    days: int = 30
    max_workers: int = 10


# --- Core Logic Functions ---

def fetch_aligned_series(project_id, start_time, end_time, full_filter):
    """Fetches 1-minute aligned sums from Cloud Monitoring."""
    project_name = f"projects/{project_id}"
    start_time = int(start_time) - (int(start_time) % 60)
    end_time = int(end_time) - (int(end_time) % 60)

    interval = monitoring_v3.TimeInterval({
        "end_time": {"seconds": int(end_time)},
        "start_time": {"seconds": int(start_time)},
    })

    aggregation = monitoring_v3.Aggregation({
        "alignment_period": {"seconds": 60},
        "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
    })

    try:
        results = monitoring_client.list_time_series(
            request={
                "name": project_name, "filter": full_filter, "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                "aggregation": aggregation
            }
        )
        data_points = {}
        for series in results:
            for point in series.points:
                ts = int(point.interval.end_time.timestamp())
                val = getattr(point.value, 'double_value', 0) or getattr(point.value, 'int64_value', 0)
                data_points[ts] = data_points.get(ts, 0) + val
        return data_points
    except exceptions.NotFound:
        return {}


def get_sla_metrics(project_id, service_type, service_name, start_time, end_time):
    """Calculates uptime percentage based on SLA logic."""
    configs = {
        'cloud_run_revision': {
            'total_metric': 'run.googleapis.com/request_count',
            'filter_base': f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service_name}"',
            'error_filter': 'metric.labels.response_code_class="5xx"'
        },
        'gcs_bucket': {
            'total_metric': 'storage.googleapis.com/api/request_count',
            'filter_base': f'resource.type="gcs_bucket" AND resource.labels.bucket_name="{service_name}"',
            'error_filter': 'metric.labels.response_code="500"'
        },
        'bigquery_project': {
            'total_metric': 'bigquery.googleapis.com/query/count',
            'filter_base': 'resource.type="bigquery_project"',
            'success_filter': 'metric.labels.state="SUCCEEDED"'
        }
    }
    conf = configs[service_type]
    total_filter = f'metric.type="{conf["total_metric"]}" AND {conf["filter_base"]}'
    total_data = fetch_aligned_series(project_id, start_time, end_time, total_filter)
    is_bq = (service_type == 'bigquery_project')

    suc_data, err_data = {}, {}
    if is_bq:
        suc_filter = f'{total_filter} AND {conf["success_filter"]}'
        suc_data = fetch_aligned_series(project_id, start_time, end_time, suc_filter)
    else:
        err_filter = f'{total_filter} AND {conf["error_filter"]}'
        err_data = fetch_aligned_series(project_id, start_time, end_time, err_filter)

    downtime_minutes = 0
    for t in range(int(start_time), int(end_time), 60):
        total = total_data.get(t, 0)
        if total < 1: continue
        errors = (total - suc_data.get(t, 0)) if is_bq else err_data.get(t, 0)
        if (errors / total) >= 1.0: downtime_minutes += 1

    total_mins = (end_time - start_time) / 60
    uptime_pct = ((total_mins - downtime_minutes) / total_mins) * 100
    return round(uptime_pct, 4), downtime_minutes


# --- Background Worker ---

def run_sla_task(job_id: str, request: ReportRequest):
    doc_ref = db.collection(COLLECTION_NAME).document(job_id)
    try:
        end_ts = int(time.time())
        start_ts = end_ts - (request.days * 24 * 60 * 60)

        tasks = [(p.id, s) for p in request.projects for s in p.services]

        with concurrent.futures.ThreadPoolExecutor(max_workers=request.max_workers) as executor:
            futures = [executor.submit(lambda x: (
            x[0], x[1].name, get_sla_metrics(x[0], x[1].type, x[1].name, start_ts, end_ts), x[1].threshold), t) for t in
                       tasks]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        formatted = {}
        for pid, name, (uptime, mins), thresh in results:
            if pid not in formatted: formatted[pid] = []
            formatted[pid].append(
                {"service": name, "uptime_pct": uptime, "downtime_minutes": mins, "compliant": uptime >= thresh})

        doc_ref.update({
            "status": "completed",
            "finished_at": datetime.now().isoformat(),
            "data": [{"project_id": k, "metrics": v} for k, v in formatted.items()]
        })
    except Exception as e:
        doc_ref.update({"status": "failed", "error": str(e)})


# --- API Endpoints ---

@app.post("/v1/compliance_report", status_code=202)
async def create_report(request: ReportRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    db.collection(COLLECTION_NAME).document(job_id).set({
        "job_id": job_id, "status": "processing", "started_at": datetime.now().isoformat(), "days": request.days
    })
    background_tasks.add_task(run_sla_task, job_id, request)
    return {"job_id": job_id}


@app.get("/v1/compliance_report")
async def list_reports():
    docs = db.collection(COLLECTION_NAME).order_by("started_at", direction=firestore.Query.DESCENDING).limit(
        10).stream()
    return [doc.to_dict() for doc in docs]


@app.get("/v1/compliance_report/{job_id}")
async def get_report(job_id: str):
    doc = db.collection(COLLECTION_NAME).document(job_id).get()
    if not doc.exists: raise HTTPException(status_code=404)
    return doc.to_dict()


# --- Dashboard HTML ---

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>SLA Compliance Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-100 p-8">
        <div class="max-w-6xl mx-auto">
            <h1 class="text-3xl font-bold mb-6">SLA Compliance Dashboard</h1>

            <div class="bg-white p-6 rounded-lg shadow mb-8">
                <h2 class="text-xl font-semibold mb-4">Run New Report</h2>
                <textarea id="config" class="w-full h-32 p-2 border rounded font-mono text-sm" placeholder='{"projects": [{"id": "prj-id", "services": [{"name": "svc", "type": "cloud_run_revision", "threshold": 99.9}]}]}'></textarea>
                <button onclick="runReport()" class="mt-4 bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700">Generate Report</button>
            </div>

            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-semibold mb-4">Recent Reports</h2>
                <div id="reports-list" class="space-y-4">Loading reports...</div>
            </div>
        </div>

        <script>
            async function loadReports() {
                const res = await fetch('/v1/compliance_report');
                const reports = await res.json();
                const container = document.getElementById('reports-list');
                container.innerHTML = reports.map(r => `
                    <div class="border-b pb-4">
                        <div class="flex justify-between items-center">
                            <div>
                                <span class="font-mono text-sm font-bold text-gray-500">${r.job_id}</span>
                                <p class="text-sm text-gray-600">${r.started_at}</p>
                            </div>
                            <span class="px-3 py-1 rounded text-sm ${r.status === 'completed' ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'}">
                                ${r.status.toUpperCase()}
                            </span>
                        </div>
                        ${r.data ? renderData(r.data) : ''}
                    </div>
                `).join('');
            }

            function renderData(data) {
                return data.map(p => `
                    <div class="mt-2 ml-4">
                        <p class="font-bold text-sm">Project: ${p.project_id}</p>
                        <ul class="text-sm">
                            ${p.metrics.map(m => `
                                <li>${m.compliant ? '✅' : '❌'} ${m.service}: ${m.uptime_pct}% (${m.downtime_minutes}m downtime)</li>
                            `).join('')}
                        </ul>
                    </div>
                `).join('');
            }

            async function runReport() {
                const config = JSON.parse(document.getElementById('config').value);
                await fetch('/v1/compliance_report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                alert('Report job started!');
                loadReports();
            }

            loadReports();
            setInterval(loadReports, 10000);
        </script>
    </body>
    </html>
    """