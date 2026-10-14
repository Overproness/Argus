def drop_expired(jobs, now, ttl=60):
    for job in list(jobs):
        if job.completed_at is not None and now - job.completed_at > ttl:
            jobs.remove(job)
    return jobs
