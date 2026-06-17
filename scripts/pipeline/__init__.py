"""Host-side clinical-data publish engine.

This package holds the trusted lower-level publish path (Dictionary →
Datasets → PHI scrub → cleanup → ``llm_source/`` publication) that was
previously inlined in the repository-root ``main.py``. After the Wave 6
"thin ``main.py``" cutover, ``main.py`` is only a launcher for the AI
assistant (``--chat`` / ``--web``); the publish engine lives here and is
invoked as a subprocess (``python -m scripts.pipeline.host_pipeline``) by
the ``dataset-to-llm-source`` skill under the orchestrator's pipeline lock.
"""
