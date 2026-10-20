# The replay library is the tracer compiled without file output: identical wrappers,
# identical wildcard numbering.
MPI ?= openmpi
MPICC := /usr/bin/mpicc.$(MPI)
CFLAGS ?= -O1 -g -Wall -Wextra
CFLAGS += -fPIC   # a shared object whatever CFLAGS the environment sets
OUT := bin/$(MPI)

all: $(OUT)/libstretto-pin.so

$(OUT)/libstretto-pin.so: ../tracer/mpitrace.c pin.c pin.h
	@mkdir -p $(OUT)
	$(MPICC) $(CFLAGS) -DSTRETTO_PIN_ONLY -shared -o $@ ../tracer/mpitrace.c pin.c -lpthread

clean:
	rm -rf bin
.PHONY: all clean
