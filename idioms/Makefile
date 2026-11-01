# Build every idiom with the SYSTEM MPI compiler wrapper (gcc underneath).
# MPI=openmpi (default) or MPI=mpich selects the implementation.
MPI ?= openmpi
MPICC := /usr/bin/mpicc.$(MPI)
CFLAGS ?= -O0 -g -Wall -Wextra
IDIOMS := $(wildcard [0-9][0-9]_*)
BINS := $(foreach d,$(IDIOMS),$(d)/bin/$(MPI)/main)
TSANLIB := $(abspath ../tracer/bin)

all: $(BINS)

# An idiom with a file "flags" (threads: -pthread or -fopenmp) is compiled with its contents.
# One with a file "tsan" is compiled with gcc's ThreadSanitizer instrumentation and the flags in
# that file, and linked without ThreadSanitizer's runtime, against the tracer's
# libstretto-tsan.so (docs/threads.md, section 17), so that the tracer sees its atomics.
%/bin/$(MPI)/main: %/main.c
	@mkdir -p $(dir $@)
	@if [ -f $*/tsan ]; then \
	  $(MAKE) -s -C ../tracer stub && \
	  $(MPICC) $(CFLAGS) $(shell cat $*/flags 2>/dev/null) -fsanitize=thread --param=tsan-instrument-func-entry-exit=0 \
	    $(shell cat $*/tsan 2>/dev/null) -c -o $@.o $< && \
	  $(MPICC) $(CFLAGS) $(shell cat $*/flags 2>/dev/null) -o $@ $@.o -lm -L$(TSANLIB) -lstretto-tsan -Wl,-rpath,$(TSANLIB) && rm -f $@.o; \
	else $(MPICC) $(CFLAGS) $(shell cat $*/flags 2>/dev/null) -o $@ $< -lm; fi

clean:
	rm -rf */bin

.PHONY: all clean
