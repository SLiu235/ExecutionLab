PYTHON ?= python3
.PHONY: test demo capture
test:
	$(PYTHON) -m unittest discover -s tests -v
demo:
	$(PYTHON) -m execution_lab demo --output results/demo
capture:
	$(PYTHON) -m execution_lab capture --product BTC-USD --samples 1800 --interval 1 --output data/captures/btc_session
