import time
import yaml
from google.api_core import exceptions
from google.cloud import monitoring_v3
from datetime import datetime

client = monitoring_v3.MetricServiceClient()


def fetch_aligned_series(project_id, start_time, end_time, full_filter):
    """Fetches 1-minute aligned sums from Cloud Monitoring."""
    project_name = f"projects/{project_id}"

    start_time = int(start_time) - (int(start_time) % 60)
    end_time = int(end_time) - (int(end_time) % 60)

    interval = monitoring_v3.TimeInterval({
        "end_time": {"seconds": int(end_time)},
        "start_time": {"seconds": int(start_time)},
    })

    # FIX: Use a dictionary for Duration to avoid 'Duration object has no attribute seconds'
    aggregation = monitoring_v3.Aggregation({
        "alignment_period": {"seconds": 60},
        "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
    })

    try:
        results = client.list_time_series(
            request={
                "name": project_name,
                "filter": full_filter,
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                "aggregation": aggregation
            }
        )

        data_points = {}
        for series in results:
            for point in series.points:
                # FIX: DatetimeWithNanoseconds conversion using .timestamp()
                ts = int(point.interval.end_time.timestamp())
                # Safely get the value regardless of type
                val = getattr(point.value, 'double_value', 0) or getattr(point.value, 'int64_value', 0)
                data_points[ts] = data_points.get(ts, 0) + val
        return data_points

    except exceptions.NotFound:
        # Bypasses the 404 error if BigQuery labels haven't been recorded yet
        print(f"Warning: No data/labels found for filter: {full_filter}")
        return {}


def get_sla_metrics(project_id, service_type, service_name, start_time, end_time):
    print("Getting SLA metrics for {}, {}".format(service_type, service_name))
    # Configuration Mapping
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
            # BQ Fix: Use status="ok" to calculate errors (Total - Success)
            'success_filter': 'metric.labels.state="SUCCEEDED"'
        }
    }

    conf = configs[service_type]

    # 1. Fetch Total Traffic
    total_filter = f'metric.type="{conf["total_metric"]}" AND {conf["filter_base"]}'
    total_data = fetch_aligned_series(project_id, start_time, end_time, total_filter)
    print(total_data)
    # 2. Fetch Error/Success Data
    is_bq = (service_type == 'bigquery_project')
    if is_bq:
        # Errors = Total - Success
        suc_filter = f'{total_filter} AND {conf["success_filter"]}'
        success_data = fetch_aligned_series(project_id, start_time, end_time, suc_filter)
    else:
        err_filter = f'{total_filter} AND {conf["error_filter"]}'
        error_data = fetch_aligned_series(project_id, start_time, end_time, err_filter)

    # 3. Process Downtime Minutes
    downtime_minutes = 0
    # Step through every minute of the period
    loop_start = int(start_time) - (int(start_time) % 60)
    loop_end = int(end_time) - (int(end_time) % 60)
    for t in range(loop_start, loop_end, 60):
        total = total_data.get(t, 0)
        if total < 1: continue

        errors = (total - success_data.get(t, 0)) if is_bq else error_data.get(t, 0)

        # Standard SLA logic: If 100% of requests in a minute fail (or specific threshold)
        if (errors / total) >= 1.0:
            downtime_minutes += 1

    total_mins = (end_time - start_time) / 60
    uptime_pct = ((total_mins - downtime_minutes) / total_mins) * 100
    return round(uptime_pct, 4), downtime_minutes


def run_report():
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # Calculate 30-day window
    end_ts = int(time.time())
    start_ts = end_ts - (30 * 24 * 60 * 60)
    # Align start to the nearest minute
    start_ts = start_ts - (start_ts % 60)

    print(f"--- SLA Report ({datetime.fromtimestamp(start_ts)} to {datetime.fromtimestamp(end_ts)}) ---")

    for project in config['projects']:
        project_id = project['id']
        for service in project['services']:
            name = service['name']
            stype = service['type']
            threshold = service['threshold']

            uptime, mins = get_sla_metrics(project_id, stype, name, start_ts, end_ts)

            status = "✅ COMPLIANT" if uptime >= threshold else "❌ NON-COMPLIANT"
            print(f"Service: {name:<25} | Uptime: {uptime}% | Downtime: {mins}m | {status}")


if __name__ == "__main__":
    run_report()

    #test