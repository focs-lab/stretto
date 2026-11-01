MPI ?= openmpi
MPICC := /usr/bin/mpicc.$(MPI)
CC := /usr/bin/gcc
CFLAGS ?= -O1 -g -Wall -Wextra
CFLAGS += -fPIC -mcx16   # a shared object whatever CFLAGS the environment sets; 16-byte atomics (tsan_rt.c)
OUT := bin/$(MPI)

# Families of MPI-4 calls the library declares, whatever MPI_VERSION says (Open MPI 5 says
# 3.1 and provides much of MPI 4.0, but not the large-count calls): one compile probe each,
# so that the tracer marks every call it cannot model instead of letting it pass unseen.
PROBES := MPI_Isendrecv:STRETTO_HAVE_MPI4 MPI_Send_c:STRETTO_HAVE_LARGE_COUNT
FEATURES := -DSTRETTO_PROBED $(shell for p in $(PROBES); do \
    printf '\043include <mpi.h>\nvoid *p = (void *)&%s;\n' "$$(echo $$p | cut -d: -f1)" | $(MPICC) -x c -c -o /dev/null - 2>/dev/null \
    && printf -- '-D%s ' "$$(echo $$p | cut -d: -f2)"; done)

all: $(OUT)/libstretto-trace.so bin/libstretto-tsan.so

$(OUT)/libstretto-trace.so: mpitrace.c tsan_rt.c ../replay/pin.c ../replay/pin.h
	@mkdir -p $(OUT)
	$(MPICC) $(CFLAGS) $(FEATURES) -shared -o $@ mpitrace.c ../replay/pin.c -lpthread

# The runtime that a program compiled with ThreadSanitizer's instrumentation links instead of
# ThreadSanitizer's own (tsan_rt.c, docs/threads.md section 17): it performs the atomic
# operations and records nothing; the tracer, preloaded, interposes its recording versions.
# Independent of the MPI.
stub: bin/libstretto-tsan.so
bin/libstretto-tsan.so: tsan_rt.c
	@mkdir -p bin
	$(CC) $(CFLAGS) -DSTRETTO_TSAN_STUB -shared -o $@ tsan_rt.c

clean:
	rm -rf bin
.PHONY: all stub clean
