# Build the paper with latexmk; outputs go to build/ (ignored by git).
# One job, no parallelism: the build host is shared.

MAIN := main
OUT  := build

.PHONY: all clean watch

all: $(OUT)/$(MAIN).pdf

$(OUT)/$(MAIN).pdf: $(wildcard *.tex sections/*.tex appendix/*.tex figures/*.tex generated/*.tex) refs.bib
	latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=$(OUT) $(MAIN).tex

watch:
	latexmk -pdf -pvc -interaction=nonstopmode -outdir=$(OUT) $(MAIN).tex

clean:
	rm -rf $(OUT)
