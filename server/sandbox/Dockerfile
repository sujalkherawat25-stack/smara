# Isolated Python sandbox for agent data work (Tally/Excel/CSV) and document making. No app code, no secrets.
FROM python:3.12-slim
# WeasyPrint needs pango/harfbuzz to shape Hindi and Gujarati correctly; the Noto fonts cover Latin, Devanagari and Gujarati.
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfontconfig1 \
        fonts-noto-core && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "pandas>=2.2,<4" "openpyxl>=3.1,<4" "xlrd>=2.0,<3" "numpy>=1.26,<3" "reportlab>=4.2,<5" "pypdf>=5,<7" \
        "weasyprint>=62,<70" "python-docx>=1.1,<2"
WORKDIR /srv
COPY sandbox/runner.py sandbox/smara_helpers.py sandbox/smara_docs.py ./
ENV SANDBOX_JOBS=/jobs PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-u", "runner.py"]
