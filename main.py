import yaml
from datetime import datetime, timedelta

from google.cloud import monitoring_v3
import time


def calculate_uptime_percentage(project_id, service_name, type, metric_path):
    client = monitoring_v3.MetricServiceClient()
    project_path = f"projects/{project_id}"

    # 30-day interval
    now = time.time()
    seconds = int(now)
    nanos = int((now - seconds) * 10 ** 9)
    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": seconds, "nanos": nanos},
            "start_time": {"seconds": seconds - (2 * 24 * 60 * 60), "nanos": nanos},
        }
    )

    # Filter for the specific Cloud Run service
    if type == 'cloud_run_revision':
        filter_base = (
            f'resource.type = "{type}" AND '
            f'resource.labels.service_name = "{service_name}" AND '
            f'metric.type = "{metric_path}"'
        )
    elif type == 'gcs_bucket':
        filter_base = (
            f'resource.type = "{type}" AND '
            f'metric.type = "{metric_path}" AND '
            f'resource.labels.bucket_name = "{service_name}"'
        )
    elif type == 'bigquery':
        f'resource.type = "{type}" AND '
        f'metric.type = "{metric_path}"'

    #run.googleapis.com/request_count

    def get_count(extra_filter=""):
        print("Filter == {}".format(filter_base + extra_filter))
        results = client.list_time_series(
            request={
                "name": project_path,
                "filter": filter_base + extra_filter,
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            }
        )

        # Sum up all points in the time series
        total = 0
        for series in results:
            for point in series.points:
                total += point.value.int64_value
        return total

    total_reqs = get_count()
    print('total_reqs : {}'.format(total_reqs))
    # Filter specifically for server-side errors (5xx)
    error_reqs = None
    if type == 'cloud_run_revision':
        error_reqs = get_count(' AND metric.labels.response_code_class = "5xx"')
    elif type == 'gcs_bucket':
        error_reqs = get_count(' AND metric.labels.response_code = starts_with("5")')

    print('error_reqs : {}'.format(error_reqs))

    if total_reqs == 0:
        return 100.0  # Assume 100% if no traffic exists

    # SLA Formula: 1 - (Errors / Total)
    uptime_percentage = (1 - (error_reqs / total_reqs)) * 100
    return round(uptime_percentage, 2)

def get_compliance_report(project_id, service_name, type, metric_path, threshold):
    client = monitoring_v3.MetricServiceClient()
    project_name = f"projects/{project_id}"

    # Define timeframe (e.g., last 30 days)
    now = datetime.utcnow()
    start_time = now - timedelta(days=30)

    # Metric: Cloud Run Request Count
    # We filter by response_code_class to identify 5xx errors
    # Logic: 1 - (Sum of 5xx / Total Requests)


    # (Implementation of metric aggregation logic here)
    #uptime_pct = 99.98  # Placeholder for calculated value
    uptime_pct = calculate_uptime_percentage(project_id, service_name, type, metric_path)

    status = "✅ COMPLIANT" if uptime_pct >= threshold else "❌ NON-COMPLIANT"

    return {
        "service": service_name,
        "uptime": f"{uptime_pct}%",
        "threshold": f"{threshold}%",
        "status": status
    }


# Load team configuration [cite: 12]
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

for project in config['projects']:
    for service in project['services']:
        report = get_compliance_report(project['id'],
                                       service['name'],
                                       service['type'],
                                       service['metric_path'],
                                       service['threshold'])
        print(f"| {report['service']} | {report['uptime']} | {report['status']} |")