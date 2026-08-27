SHELL := /bin/bash

.PHONY: all results-figures clean check

all:
	mkdir -p build
	latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build main.tex

results-figures:
	python3 scripts/generate_results_figures.py

check: all
	pdfinfo build/main.pdf | grep -E 'Pages:|Page size:'
	pdffonts build/main.pdf

clean:
	latexmk -C -outdir=build main.tex
