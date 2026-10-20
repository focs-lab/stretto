MPI ?= openmpi
MPICC := /usr/bin/mpicc.$(MPI)
CFLAGS ?= -O1 -g -Wall -Wextra
CFLAGS += -fPIC   # a shared object whatever CFLAGS the environment sets
OUT := bin/$(MPI)

all: $(OUT)/libstretto-trace.so

$(OUT)/libstretto-trace.so: mpitrace.c ../replay/pin.c ../replay/pin.h
	@mkdir -p $(OUT)
	$(MPICC) $(CFLAGS) -shared -o $@ mpitrace.c ../replay/pin.c -lpthread

clean:
	rm -rf bin
.PHONY: all clean
