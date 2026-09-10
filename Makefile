PYTHON ?= python3

.PHONY: test
test:
	$(PYTHON) -m pytest backend/tests -q
