"""Skills package for RePORT AI Portal.

Houses the CLI helpers that wrap the pipeline operations exposed to operators
and automation. Each module in this package is a self-contained skill that can
be called from the command line or programmatically.

Current skills:
    * :mod:`.extract_to_llm_source` — operationally untraceable staging removal
      with destruction attestation.
"""
