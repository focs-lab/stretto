#!/bin/bash -l
# A one-node probe of a compute node's software and hardware: the facts behind the table
# "The SoC cluster" in docs/cluster.md. Submit from a login node, a few minutes on one node:
#   sbatch -p normal -N 1 -n 1 -c 4 --mem=4G --time=00:10:00 -C <node family> \
#       -o build/cluster/probe-%j.out experiments/cluster/probe.sh
# Prints one section per topic; a missing tool prints "-" or its error, never stops the probe.
sec() { echo; echo "##### $*"; }
sec host; hostname; date -Is; uname -a; cat /etc/os-release | head -4; ldd --version | head -1
sec cpu; lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Core|Socket|NUMA node\(s\)|Flags' | cut -c1-200
sec mem; free -g
sec slurm-env; env | grep -E '^SLURM_(JOB_ID|NODELIST|CPUS_ON_NODE|JOB_PARTITION|MEM)' | sort
sec cgroup; cat /proc/self/cgroup; nproc
sec containers; for c in apptainer singularity docker podman charliecloud ch-run enroot; do printf '%s: ' $c; command -v $c || echo -; done; apptainer --version 2>&1 | head -1
sec modules; type module 2>&1 | head -1; module avail 2>&1 | head -100; echo "MODULEPATH=$MODULEPATH"; ls /opt /usr/local /apps /software 2>/dev/null | head -40
sec mpi; for c in mpirun mpiexec mpicc mpichversion ompi_info srun pmix_info prterun; do printf '%s: ' $c; command -v $c || echo -; done
mpirun --version 2>&1 | head -2; ompi_info 2>/dev/null | grep -E 'Open MPI:|MCA (pml|btl|plm|ess|pmix)' | head -20
srun --version; srun --mpi=list 2>&1
ls /usr/lib/x86_64-linux-gnu | grep -iE 'pmix|pmi|slurm|ucx|fabric|ibverbs' | head -20
dpkg -l 2>/dev/null | grep -iE 'openmpi|mpich|libpmix|ucx|libfabric|rdma|ibverbs|apptainer|singularity' | awk '{print $2,$3}'
sec compilers; for c in gcc g++ gfortran clang cmake make python3 python3.12 spack git; do printf '%s: ' $c; command -v $c && $c --version 2>&1 | head -1; done
sec net; ip -br link; ip -br addr; for i in $(ls /sys/class/net); do printf '%s mtu=%s speed=%s\n' $i "$(cat /sys/class/net/$i/mtu)" "$(cat /sys/class/net/$i/speed 2>/dev/null)"; done
ls /sys/class/infiniband 2>&1; ibv_devinfo 2>&1 | head -20; ucx_info -v 2>&1 | head -3; ucx_info -d 2>&1 | grep -E 'Transport|Device' | head -20; fi_info -l 2>&1 | head -20
sec disk; df -hT / /tmp /home ~ /scratch /local /localdisk /raid /export 2>/dev/null; echo "TMPDIR=$TMPDIR"; lsblk -o NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null | head -20; mount | grep -E ' /(tmp|scratch|local)' 
sec limits; ulimit -a | grep -E 'locked|open files|stack|processes'; cat /proc/sys/kernel/yama/ptrace_scope /proc/sys/kernel/perf_event_paranoid /proc/sys/kernel/randomize_va_space 2>&1
sec quota; quota -s 2>&1 | head; df -h ~ | tail -1
