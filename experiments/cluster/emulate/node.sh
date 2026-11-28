#!/bin/sh
# Entry point of an emulated node: the account of the user who started the emulation (NODE_UID,
# NODE_GID), munge with the emulation's key (/keys/munge.key), the Slurm configuration of the
# nodes NODES (default "node1 node2", CPUS each, the first the controller), slurmctld on the
# first node, slurmd on every node; then it waits. The nodes carry the SoC cluster's features
# (xcni, xcnf) and its partitions (normal, long) beside emu, so that slurm.sbatch's defaults
# and experiments/cluster/submit-scale.sh's options are valid here.
set -e
NODES="${NODES:-node1 node2}"; CPUS="${CPUS:-8}"
first="${NODES%% *}"
groupadd -o -g "$NODE_GID" mpi 2>/dev/null || true
useradd -o -u "$NODE_UID" -g "$NODE_GID" -m -d /home/mpi -s /bin/bash mpi 2>/dev/null || true
mkdir -p /etc/munge /run/munge /var/lib/munge /var/log/munge /etc/slurm /var/spool/slurmd /var/spool/slurmctld
cp /keys/munge.key /etc/munge/munge.key
chown -R munge:munge /etc/munge /run/munge /var/lib/munge /var/log/munge
chmod 0400 /etc/munge/munge.key
runuser -u munge -- /usr/sbin/munged
cat > /etc/slurm/slurm.conf <<EOF
ClusterName=emulation
SlurmctldHost=$first
SlurmUser=root
AuthType=auth/munge
CredType=cred/munge
ProctrackType=proctrack/linuxproc
TaskPlugin=task/none
SelectType=select/cons_tres
SelectTypeParameters=CR_Core
SlurmdParameters=config_overrides
MpiDefault=none
ReturnToService=2
JobAcctGatherType=jobacct_gather/none
AccountingStorageType=accounting_storage/none
StateSaveLocation=/var/spool/slurmctld
SlurmdSpoolDir=/var/spool/slurmd
SlurmctldPidFile=/run/slurmctld.pid
SlurmdPidFile=/run/slurmd.pid
SlurmctldLogFile=/var/log/slurmctld.log
SlurmdLogFile=/var/log/slurmd.log
NodeName=$(echo "$NODES" | tr ' ' ',') CPUs=$CPUS Features=xcni,xcnf State=UNKNOWN
PartitionName=emu Nodes=ALL Default=YES MaxTime=INFINITE State=UP OverSubscribe=NO
PartitionName=normal Nodes=ALL MaxTime=INFINITE State=UP OverSubscribe=NO
PartitionName=long Nodes=ALL MaxTime=INFINITE State=UP OverSubscribe=NO
EOF
# slurmd 23.11 puts its step daemons into a cgroup of their own, which systemd makes on a real
# node; here the container's own cgroup tree (a private cgroup namespace, remounted writable)
# holds it, with this script's processes moved into a leaf first (cgroup v2 lets only leaves
# hold processes). Nothing is constrained.
mount -o remount,rw /sys/fs/cgroup
mkdir -p /sys/fs/cgroup/init /sys/fs/cgroup/system.slice
for p in $(cat /sys/fs/cgroup/cgroup.procs); do echo "$p" > /sys/fs/cgroup/init/cgroup.procs 2>/dev/null || true; done
printf 'CgroupPlugin=cgroup/v2\nIgnoreSystemd=yes\nConstrainCores=no\nConstrainRAMSpace=no\nConstrainDevices=no\n' \
    > /etc/slurm/cgroup.conf
[ "$(hostname)" = "$first" ] && /usr/sbin/slurmctld
/usr/sbin/slurmd
exec sleep infinity
