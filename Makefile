PY ?= .venv/Scripts/python
ifeq ($(OS),)
PY := .venv/bin/python
endif

.PHONY: demo install ingest sample seed reset-demo test api web build

demo:            ## one command: venv + data + seed + API + UI
	python scripts/demo.py

install:
	python -m venv .venv && $(PY) -m pip install -r requirements.txt && cd frontend && npm install

sample:
	$(PY) scripts/generate_sample.py

ingest:
	$(PY) scripts/ingest.py

seed:
	$(PY) scripts/seed_demo.py

reset-demo:
	$(PY) scripts/seed_demo.py --reset

test:
	$(PY) -m pytest -q

api:
	$(PY) -m uvicorn fundbridge.api:app --reload --port 8000

web:
	cd frontend && npm run dev

build:          ## single-port mode: API serves frontend/dist at :8000
	cd frontend && npm run build
