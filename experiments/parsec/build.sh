#!/usr/bin/env bash
# Build PaRSEC with MPI and its reshape tests (tests/collections/reshape) with the driver
# reshape_check added, plain and with self-tags.patch applied. Run inside a container of the
# image built from Dockerfile here, with the work directory mounted at /h (source
# /h/src/parsec, build trees /h/build/parsec-{plain,fixed}):
#   docker run --rm --name hunt2-build --cpuset-cpus 0-5,40-45 --memory 20g \
#     --user "$(id -u):$(id -g)" -e HOME=/tmp -v ~/thr-hunt2:/h -w /h hunt2-parsec:24.04 \
#     /h/repo/experiments/parsec/build.sh [plain] [fixed]
# JOBS (default 6) bounds the parallel jobs. TSAN=1 compiles PaRSEC with clang 18's
# ThreadSanitizer instrumentation of atomic operations and links it against the tracer's
# libstretto-tsan.so (docs/threads.md section 17; the image of experiments/atomics/Dockerfile),
# into /h/build-tsan/parsec-{plain,fixed} (traced.sh takes that directory as BUILD).
set -euo pipefail
JOBS=${JOBS:-6}; TSAN=${TSAN:-0}
S=/h/src/parsec E=/h/repo/experiments/parsec BR=/h/build
CC_=/usr/bin/gcc CXX_=/usr/bin/g++ TSANF=()
if [ "$TSAN" = 1 ]; then
  make -s -C /h/repo/tracer stub
  BR=/h/build-tsan CC_=/usr/bin/clang-18 CXX_=/usr/bin/clang++-18
  t="-fsanitize=thread -fno-sanitize-link-runtime -mllvm -tsan-instrument-memory-accesses=0 -mllvm -tsan-instrument-func-entry-exit=0 -mllvm -tsan-instrument-memintrinsics=0 -Wno-unused-command-line-argument"
  l="-Wl,--no-as-needed -L/h/repo/tracer/bin -lstretto-tsan -Wl,-rpath,/h/repo/tracer/bin"
  TSANF=(-DCMAKE_C_FLAGS="$t" -DCMAKE_CXX_FLAGS="$t" -DCMAKE_EXE_LINKER_FLAGS="$l" -DCMAKE_SHARED_LINKER_FLAGS="$l")
fi
variants=("$@"); [ ${#variants[@]} = 0 ] && variants=(plain fixed)
git -C $S log -1 --format='PaRSEC %h %cd'
targets=(reshape input_dep_reshape_single_copy remote_multiple_outs_same_pred_flow avoidable_reshape
         reshape_check)
for v in "${variants[@]}"; do
  src=$BR/parsec-$v-src B=$BR/parsec-$v
  mkdir -p $BR
  rm -rf $src && cp -r $S $src
  [ "$v" = fixed ] && patch -s -d $src -p1 < $E/self-tags.patch
  cp $E/reshape_check.jdf $E/testing_reshape_check.c $src/tests/collections/reshape/
  printf '%s\n' 'parsec_addtest_executable(C reshape_check SOURCES testing_reshape_check.c common.c)' \
    'target_ptg_sources(reshape_check PRIVATE "reshape_check.jdf;")' >> $src/tests/collections/reshape/CMakeLists.txt
  cmake -G "Unix Makefiles" -S $src -B $B -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_C_COMPILER=$CC_ -DCMAKE_CXX_COMPILER=$CXX_ ${TSANF[@]+"${TSANF[@]}"} \
    -DPARSEC_DIST_WITH_MPI=ON -DPARSEC_GPU_WITH_CUDA=OFF -DPARSEC_GPU_WITH_HIP=OFF \
    -DPARSEC_GPU_WITH_LEVEL_ZERO=OFF -DPARSEC_PROF_TRACE=OFF -DBUILD_TESTING=ON > $B.cmake.log
  cmake --build $B -j "$JOBS" --target "${targets[@]}"
  find $B -name '*.o' -delete
done
