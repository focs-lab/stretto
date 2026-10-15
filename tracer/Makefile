MPI ?= openmpi
MPICC := /usr/bin/mpicc.$(MPI)
CFLAGS ?= -O1 -g -Wall -Wextra -fPIC
OUT := bin/$(MPI)

all: $(OUT)/librematch-trace.so

$(OUT)/librematch-trace.so: mpitrace.c ../replay/pin.c ../replay/pin.h
	@mkdir -p $(OUT)
	$(MPICC) $(CFLAGS) -shared -o $@ mpitrace.c ../replay/pin.c -lpthread

clean:
	rm -rf bin
.PHONY: all clean
