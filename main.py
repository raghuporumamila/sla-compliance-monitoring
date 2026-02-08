import time

import yaml
from google.api_core import exceptions
from google.cloud import monitoring_v3

client = monitoring_v3.MetricServiceClient()


def get_configs(service_name, service_type, metric_path, threshold):
    configs = {
        'cloud_run_revision': {
            'total_metric': f"{metric_path}",
            'filter_base': f'resource.type="{service_type}" AND resource.labels.service_name="{service_name}"',
            'error_filter': 'metric.labels.response_code_class="5xx"',
            'threshold': threshold,
            'min_reqs': 100
        },
        'bigquery_project': {
            'total_metric': f"{metric_path}",
            'filter_base': f'resource.type="{service_type}"',
            # Shift to identifying success to avoid the 'statement_status' label error
            'success_filter': 'metric.labels.statement_status="ok"',
            'threshold': threshold,
            'min_reqs': 20
        },
        'gcs_bucket': {
            'total_metric': f"{metric_path}",
            f'filter_base': f'resource.type="{service_type}" AND resource.labels.bucket_name="{service_name}"',
            'error_filter': 'metric.labels.response_code=starts_with("5")',
            'threshold': threshold,
            'min_reqs': 1
        }
    }
    return configs


def fetch_points(project_id, start_time, end_time, full_filter):
    project_name = f"projects/{project_id}"
    try:
        # Define the Aggregation object correctly
        aggregation = monitoring_v3.Aggregation({
            "alignment_period": {"seconds": 60},  # 1-minute windows
            "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
        })

        interval = monitoring_v3.TimeInterval({
            "start_time": {"seconds": int(start_time)},
            "end_time": {"seconds": int(end_time)},
        })
        results = client.list_time_series(
            request={
                "name": project_name,
                "filter": full_filter,
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                "aggregation": aggregation
            }
        )

        points_map = {}
        for ts in results:
            for p in ts.points:
                # Convert DatetimeWithNanoseconds to a Unix timestamp integer
                ts_seconds = int(p.interval.start_time.timestamp())

                # Extract value correctly
                val = p.value.double_value if 'double_value' in str(p.value) else p.value.int64_value
                points_map[ts_seconds] = points_map.get(ts_seconds, 0) + val
        return points_map
    except exceptions.NotFound:
        # If the metric or label doesn't exist yet, it means 0 data points
        print(f"Note: No data found for filter: {full_filter}")
        return {}


def get_sla_metrics(project_id, service_type, service_name, metric_path, threshold, start_time, end_time):
    print("service_name == {}, service_type == {}".format(service_type, service_name))
    # Configuration (Logic remains the same)
    conf = get_configs(service_name, service_type, metric_path, threshold)[service_type]

    total_data = fetch_points(project_id, start_time, end_time,
                              f'metric.type="{conf["total_metric"]}" AND {conf["filter_base"]}')

    error_data = {}
    if service_type == 'bigquery_project':
        # For BQ: Errors = Total - Successes
        success_data = fetch_points(project_id, start_time, end_time,
                                    f'metric.type="{conf["total_metric"]}" AND {conf["filter_base"]} AND {conf["success_filter"]}')
        for t, total in total_data.items():
            successes = success_data.get(t, 0)
            error_data[t] = total - successes
    else:
        # For Run and GCS: Fetch errors directly
        error_data = fetch_points(project_id, start_time, end_time,
                                  f'metric.type="{conf["total_metric"]}" AND {conf["filter_base"]} AND {conf["error_filter"]}')

    # Calculation logic
    total_minutes_in_period = int((end_time - start_time) / 60)
    downtime_minutes = 0

    for t in range(int(start_time), int(end_time), 60):
        total = total_data.get(t, 0)
        errors = error_data.get(t, 0)

        if total >= conf['min_reqs']:
            if (errors / total) > conf['threshold']:
                downtime_minutes += 1

    uptime_pct = ((total_minutes_in_period - downtime_minutes) / total_minutes_in_period) * 100
    return round(uptime_pct, 4), downtime_minutes


def get_compliance_report(project_id, service_name, metric_type, metric_path, threshold):
    # Get last 30 days
    end_ts = time.time()
    start_ts = end_ts - (30 * 24 * 60 * 60)

    uptime, mins = get_sla_metrics(project_id, metric_type, service_name, metric_path, threshold, start_ts, end_ts)
    print(f"Uptime: {uptime}% | Downtime: {mins} minutes")


# Load team configuration [cite: 12]
with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)

for project in config['projects']:
    for service in project['services']:
        get_compliance_report(project['id'],
                              service['name'],
                              service['type'],
                              service['metric_path'],
                              service['threshold'])
