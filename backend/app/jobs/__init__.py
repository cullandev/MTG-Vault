"""Scheduled background jobs.

Each job is a plain async function wrapped by :func:`app.jobs.runner.job_run`, so it
records a ``job_runs`` row and cannot take the scheduler down when it fails.
"""
