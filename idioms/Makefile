# Build every idiom with the SYSTEM MPI compiler wrapper (gcc underneath).
# MPI=openmpi (default) or MPI=mpich selects the implementation.
MPI ?= openmpi
MPICC := /usr/bin/mpicc.$(MPI)
CFLAGS ?= -O0 -g -Wall -Wextra
IDIOMS := $(wildcard [0-9][0-9]_*)
BINS := $(foreach d,$(IDIOMS),$(d)/bin/$(MPI)/main)

all: $(BINS)

%/bin/$(MPI)/main: %/main.c
	@mkdir -p $(dir $@)
	$(MPICC) $(CFLAGS) -o $@ $< -lm

clean:
	rm -rf */bin

.PHONY: all clean
