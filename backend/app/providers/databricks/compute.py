"""
Compute target descriptors for Databricks job submissions.

A `ComputeSpec` describes *where* a job should run. The shape is intentionally
flexible so callers can pick the right trade-off between cold-start latency,
cost, and isolation:

  * ``None``                            – serverless compute (Databricks-managed)
  * ``ComputeSpec(new_cluster=...)``    – ephemeral job cluster created per run
  * ``ComputeSpec(existing_cluster_id)``– reuse a long-running all-purpose cluster
  * ``ComputeSpec(instance_pool_id)``   – pull a worker from an instance pool

Only one of ``new_cluster``, ``existing_cluster_id`` and ``instance_pool_id``
may be set; ``DatabricksProvider.submit_job`` validates that invariant before
calling the Jobs API.

This abstraction exists primarily so control-plane workloads (email, LDAP,
anything that needs PrivateLink-style network reachability) can target classic
compute today and migrate to instance pools or always-on clusters later
without touching every caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# We use plain dicts instead of importing databricks.sdk.service.compute.ClusterSpec
# here so this module can be imported in environments without the SDK installed
# (e.g. lightweight unit tests). DatabricksProvider materialises the dicts into
# SDK objects at submit time.
ClusterDict = Dict[str, Any]


@dataclass
class ComputeSpec:
    """Where a Databricks job should run.

    Exactly zero or one of the cluster fields below may be set. ``None`` for all
    of them is interpreted as serverless compute by the SDK.

    Attributes:
        new_cluster: Inline job-cluster spec. Mirrors ``jobs.ClusterSpec`` /
            ``compute.ClusterSpec`` as a dict (so this module stays SDK-free).
            Typical keys: ``spark_version``, ``node_type_id``, ``num_workers``.
        existing_cluster_id: ID of an existing all-purpose or job cluster.
            Lowest latency option (no cold start) but the cluster must be
            running and reachable from the job.
        instance_pool_id: ID of an instance pool to draw a job cluster from.
            Faster cold start than ``new_cluster`` because the pool keeps a
            small reserve of warm VMs.
        libraries: Optional list of library specs (PyPI, Maven, Wheel, etc.)
            to install on the cluster before the task runs. Each entry is a
            dict matching ``jobs.compute.Library``'s shape.
    """

    new_cluster: Optional[ClusterDict] = None
    existing_cluster_id: Optional[str] = None
    instance_pool_id: Optional[str] = None
    libraries: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        modes = [
            ("new_cluster", self.new_cluster),
            ("existing_cluster_id", self.existing_cluster_id),
            ("instance_pool_id", self.instance_pool_id),
        ]
        set_modes = [name for name, value in modes if value]
        if len(set_modes) > 1:
            raise ValueError(
                "ComputeSpec accepts at most one of new_cluster, "
                f"existing_cluster_id, instance_pool_id; got {set_modes}."
            )

    @property
    def is_serverless(self) -> bool:
        """True when no cluster mode is set; the job will use serverless."""
        return not (self.new_cluster or self.existing_cluster_id or self.instance_pool_id)

    def to_submit_task_fields(self) -> Dict[str, Any]:
        """Project this spec onto the kwargs ``jobs.SubmitTask`` accepts.

        Returns an empty dict for serverless so the SubmitTask field defaults
        kick in. The provider is responsible for converting ``new_cluster``
        and ``instance_pool_id`` into the SDK's typed wrappers.
        """
        fields: Dict[str, Any] = {}
        if self.existing_cluster_id:
            fields["existing_cluster_id"] = self.existing_cluster_id
        elif self.new_cluster:
            fields["new_cluster"] = dict(self.new_cluster)
        elif self.instance_pool_id:
            # Express a pool-backed cluster as a single-worker new_cluster
            # pulling from the pool. The Jobs API does not have a top-level
            # `instance_pool_id` field on SubmitTask; it must live inside a
            # cluster spec.
            fields["new_cluster"] = {
                "instance_pool_id": self.instance_pool_id,
                "num_workers": 0,
                # spark_version is required even when using a pool, but the
                # caller can override by passing new_cluster directly.
            }
        if self.libraries:
            fields["libraries"] = list(self.libraries)
        return fields


def default_classic_compute(
    *,
    spark_version: Optional[str] = None,
    node_type_id: Optional[str] = None,
    num_workers: Optional[int] = None,
    existing_cluster_id: Optional[str] = None,
    instance_pool_id: Optional[str] = None,
) -> ComputeSpec:
    """Build the default compute spec for control-plane workloads (email/LDAP).

    Resolution precedence:
      1. ``existing_cluster_id`` if set – fastest, no cold start.
      2. ``instance_pool_id`` if set – warm pool, ~10–30s cold start.
      3. Otherwise an inline single-node job cluster spec.

    The function does not read ``app.core.config`` directly so it remains usable
    from environments where settings can't be imported (tests, scripts). Pass
    the relevant settings in from the caller.
    """
    if existing_cluster_id:
        return ComputeSpec(existing_cluster_id=existing_cluster_id)
    if instance_pool_id:
        # Pools still need a spark_version; default to the LTS-style fallback
        # if the caller didn't pin one.
        cluster: ClusterDict = {
            "instance_pool_id": instance_pool_id,
            "num_workers": num_workers if num_workers is not None else 0,
            "spark_version": spark_version or "15.4.x-scala2.12",
        }
        return ComputeSpec(new_cluster=cluster)

    cluster = {
        "spark_version": spark_version or "15.4.x-scala2.12",
        "node_type_id": node_type_id or "i3.xlarge",
        "num_workers": num_workers if num_workers is not None else 0,
    }
    # num_workers=0 means single-node, which Databricks requires the
    # SingleNode custom tag and a specific spark conf to accept.
    if cluster["num_workers"] == 0:
        cluster["spark_conf"] = {
            "spark.databricks.cluster.profile": "singleNode",
            "spark.master": "local[*]",
        }
        cluster["custom_tags"] = {"ResourceClass": "SingleNode"}
    return ComputeSpec(new_cluster=cluster)
