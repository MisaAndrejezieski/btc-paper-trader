PYTHON ?= python

.PHONY: test lint format run

test:
	$(PYTHON) -m unittest discover tests

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

run:
	$(PYTHON) main.py

