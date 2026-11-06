"""Required stages of one explicitly owned basic managed-S3 runtime run."""
STAGES = (
    "preflight", "smoke", "idle", "after-idle", "active",
    "worker.inject", "worker.observe", "worker.recover", "worker.release", "worker.verify",
    "partition.inject", "partition.confirm", "partition.observe", "partition.heal", "partition.finish",
    "partition.restore", "partition.verify", "load.prepare", "load.single", "load.dual", "load.verify", "final",
)
REQUIRED_CHECKS = tuple("runtime." + stage for stage in STAGES)
SCOPE = "owned-basic-managed-s3-synthetic"
