MPI ?= openmpi
MPICC := /usr/bin/mpicc.$(MPI)
CFLAGS ?= -O1 -g -Wall -Wextra
CFLAGS += -fPIC   # a shared object whatever CFLAGS the environment sets
OUT := bin/$(MPI)

# Families of MPI-4 calls the library declares, whatever MPI_VERSION says (Open MPI 5 says
# 3.1 and provides much of MPI 4.0, but not the large-count calls): one compile probe each,
# so that the tracer marks every call it cannot model instead of letting it pass unseen.
PROBES := MPI_Isendrecv:STRETTO_HAVE_MPI4 MPI_Send_c:STRETTO_HAVE_LARGE_COUNT
FEATURES := -DSTRETTO_PROBED $(shell for p in $(PROBES); do \
    printf '\043include <mpi.h>\nvoid *p = (void *)&%s;\n' "$$(echo $$p | cut -d: -f1)" | $(MPICC) -x c -c -o /dev/null - 2>/dev/null \
    && printf -- '-D%s ' "$$(echo $$p | cut -d: -f2)"; done)

all: $(OUT)/libstretto-trace.so

$(OUT)/libstretto-trace.so: mpitrace.c ../replay/pin.c ../replay/pin.h
	@mkdir -p $(OUT)
	$(MPICC) $(CFLAGS) $(FEATURES) -shared -o $@ mpitrace.c ../replay/pin.c -lpthread

clean:
	rm -rf bin
.PHONY: all clean
