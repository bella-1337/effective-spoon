# Presidio analyzer with German and French spaCy pipelines added to the
# upstream image's English one. Used by presidio-analyzer; its configuration
# stays in mounted files.
FROM mcr.microsoft.com/presidio-analyzer:2.2.362@sha256:286e3fa7f3a7426e775e8564fe1870f1ba8f999d3ab8bbb8cc46a44355d9d6e9

USER 0
COPY presidio/spacy-models.txt /tmp/spacy-models.txt
# The wheels have no dependencies beyond the image's own spaCy 3.8.
RUN pip install --no-cache-dir --no-deps --require-hashes -r /tmp/spacy-models.txt \
    && rm /tmp/spacy-models.txt
USER 1001:1001
