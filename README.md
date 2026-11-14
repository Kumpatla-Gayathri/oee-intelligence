\# OEE Intelligence



An end-to-end application for calculating and monitoring Overall Equipment Effectiveness (OEE).



\## Features

\- Availability, Performance, Quality and OEE calculations.

\- Operational anomaly detection.

\- Data-quality warnings.

\- Alert evidence and traceability.

\- Investigation management.

\- FastAPI backend and interactive dashboard.



\## OEE Formulas

\- Availability = Operating Time / Planned Production Time

\- Performance = Ideal Production Time / Operating Time

\- Quality = Good Count / Total Count

\- OEE = Availability × Performance × Quality

\- Good Count = Total Count − Scrap Count − Rework Count



\## Technology Stack

\- Python

\- FastAPI

\- SQLAlchemy

\- SQLite

\- HTML, CSS, JavaScript

\- Pytest



\## Run the Project



```powershell

pip install -r requirements.txt

$env:DATABASE\_URL = "sqlite:///data/oee\_demo.db"

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

```



Dashboard: http://127.0.0.1:8000



API documentation: http://127.0.0.1:8000/docs



\## Run Tests



```powershell

python -m pytest -q

```



Latest verified result: 124 tests passed.



\## Evaluation

Data-quality and operational anomaly evaluation are handled separately. Operational precision and recall require reliable operational ground-truth labels.



\## Limitations

\- Authentication is not implemented.

\- API pagination is not implemented.

\- Investigation ID generation is intended for a single-process demo.

