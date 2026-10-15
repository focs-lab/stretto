# The replay library is the tracer compiled without file output: identical wrappers,
# identical wildcard numbering.
MPI ?= openmpi
MPICC := /usr/bin/mpicc.$(MPI)
CFLAGS ?= -O1 -g -Wall -Wextra -fPIC
OUT := bin/$(MPI)

all: $(OUT)/librematch-pin.so

$(OUT)/librematch-pin.so: ../tracer/mpitrace.c pin.c pin.h
	@mkdir -p $(OUT)
	$(MPICC) $(CFLAGS) -DREMATCH_PIN_ONLY -shared -o $@ ../tracer/mpitrace.c pin.c -lpthread

clean:
	rm -rf bin
.PHONY: all clean
