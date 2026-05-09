.PHONY: test build clean all info setup

VENV    := .venv
ifeq ($(OS),Windows_NT)
PYTHON  ?= $(VENV)/Scripts/python
else
PYTHON  ?= $(VENV)/bin/python
endif

setup:
	python3 -m venv $(VENV) || python -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) build.py test

build:
	$(PYTHON) build.py build

all:
	$(PYTHON) build.py all

clean:
	$(PYTHON) build.py clean

info:
	$(PYTHON) build.py info
