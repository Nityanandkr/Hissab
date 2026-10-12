"""Hisaab - AI-powered personal finance intelligence for UPI / bank statements.

Everything runs locally: a Flask app, an SQLite file, pandas for tables and scikit-learn for the
classifiers. The code is organised as

    ingest/   statement files  -> clean rows
    core/     the "intelligence": parse, normalise, kind, category, learn, analytics, recurring, anomalies
    nlq/      natural-language questions -> a structured query plan -> an executed, explainable answer
    web/      Flask JSON API + the single-page UI
    synth/    a synthetic-statement generator (with hidden ground truth) used for demos and evaluation
"""

__version__ = "1.0.0"
