.PHONY: test build clean all info setup

VENV    := .venv
PYTHON  ?= $(VENV)/Scripts/python

setup:
	python -m venv $(VENV)
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
