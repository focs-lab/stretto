# A node of the two-node emulation (experiments/cluster/emulate/run.sh): the experiment image with
# Slurm 23.11 (slurmd on every node, slurmctld on node1, munge), from the same archive snapshot,
# so that jobs are submitted with sbatch and ranks start with srun through Slurm's PMIx or PMI-2
# server, as on the cluster, whose compute nodes take no ssh. No ssh server runs.
# The image also has a stand-in for apptainer (emulate/apptainer): "apptainer exec [options]
# IMAGE command" runs the command as it is, since the node already is the image, so that the
# job's container prefix is exercised.
# Build: docker build -t stretto-slurm:24.04 experiments/cluster/emulate   (needs stretto-exp:24.04)
FROM stretto-exp:24.04
ARG SNAPSHOT=20261007T095400Z
RUN apt-get update --snapshot "$SNAPSHOT" \
 && apt-get install -y --no-install-recommends --snapshot "$SNAPSHOT" slurmd slurmctld slurm-client munge \
 && rm -rf /var/lib/apt/lists/*
COPY node.sh /usr/local/sbin/stretto-node
COPY apptainer /usr/local/bin/apptainer
ENTRYPOINT ["/usr/local/sbin/stretto-node"]
