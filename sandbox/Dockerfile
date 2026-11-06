# Isolated Python sandbox for agent data work (Tally/Excel/CSV). No app code, no secrets.
FROM python:3.12-slim
RUN pip install --no-cache-dir "pandas>=2.2,<4" "openpyxl>=3.1,<4" "xlrd>=2.0,<3" "numpy>=1.26,<3" "reportlab>=4.2,<5" "pypdf>=5,<7"
WORKDIR /srv
COPY sandbox/runner.py sandbox/munim_helpers.py ./
ENV SANDBOX_JOBS=/jobs PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-u", "runner.py"]
