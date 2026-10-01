"""Unity Catalog tag changes: evaluate, submit, and find what to tag.

Backs the admin Tag Management page (``api/v1/tags.py``). ``engine`` plans a
change against live state and runs the policy, hygiene and risk checks before
applying it or opening its pull request; ``datasets`` resolves governed datasets
to their tables; ``search`` powers the page's quick search.
"""
