import logging

logger = logging.getLogger(__name__)


def send_monthly_report(payload):
    logger.info(
        "Sending monthly report to customer=%s report=%s",
        payload.get("customer"),
        payload.get("report"),
    )
    return {
        "message": "Monthly report sent",
        "customer": payload.get("customer"),
        "report": payload.get("report"),
    }


def generate_report(payload):
    logger.info(
        "Generating %s report for customer=%s",
        payload.get("report_type", "standard"),
        payload.get("customer"),
    )
    return {
        "message": "Report generated",
        "customer": payload.get("customer"),
    }


def cleanup_database(payload):
    logger.info(
        "Running database cleanup for environment=%s",
        payload.get("environment", "default"),
    )
    return {
        "message": "Database cleanup completed",
    }


def test_job(payload):
    return {"message": "Test job completed"}


def fail_job(payload):
    raise Exception("Simulated Failure")


def cron_job(payload):
    return {"message": "Cron job completed"}


HANDLERS = {
    "send_monthly_report": send_monthly_report,
    "generate_report": generate_report,
    "cleanup_database": cleanup_database,
    "test_job": test_job,
    "fail_job": fail_job,
    "cron_job": cron_job,
}


def execute_handler(job):
    handler = HANDLERS.get(job.name)

    if handler is None:
        raise ValueError(f"No handler registered for job '{job.name}'")

    return handler(job.payload)
