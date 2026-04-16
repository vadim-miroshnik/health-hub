.PHONY: install install-dev test test-all coverage lint daily backfill status auth clean

PYTHON = .venv/bin/python
HHUB   = .venv/bin/hhub

install:
	python3 -m venv .venv
	.venv/bin/pip install -e .

install-dev:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]" -r requirements-dev.txt

test:
	.venv/bin/pytest tests/unit tests/integration

test-all:
	.venv/bin/pytest tests/

coverage:
	.venv/bin/pytest --cov=src --cov-report=html --cov-report=term-missing tests/unit tests/integration

lint:
	.venv/bin/python -m py_compile src/*.py src/cli/*.py mcp_server/*.py auth/*.py

daily:
	$(HHUB) daily

backfill:
	$(HHUB) backfill

status:
	$(HHUB) status

auth:
	$(PYTHON) auth/oauth_setup.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete
	rm -rf .coverage htmlcov/ dist/ build/ *.egg-info/
