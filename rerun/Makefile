# The re-execution shim: one rank of a traced program runs alone and this library answers its
# MPI calls from a script (see rerun.c). Built with the system MPI wrapper.
MPI ?= openmpi
MPICC := /usr/bin/mpicc.$(MPI)
CFLAGS ?= -O1 -g -Wall -Wextra
CFLAGS += -fPIC   # a shared object whatever CFLAGS the environment sets
OUT := bin/$(MPI)

all: $(OUT)/libstretto-rerun.so

$(OUT)/libstretto-rerun.so: rerun.c
	@mkdir -p $(OUT)
	$(MPICC) $(CFLAGS) -shared -o $@ rerun.c

clean:
	rm -rf bin
.PHONY: all clean
