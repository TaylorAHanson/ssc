"""Unit tests for ``app.providers.databricks.compute`` (no SDK round-trip).

These tests exercise the pure-Python ``ComputeSpec`` model and the
``default_classic_compute`` factory. They intentionally don't import the
Databricks SDK — that path is covered by integration tests.
"""

import pytest

from app.providers.databricks.compute import (
    ComputeSpec,
    default_classic_compute,
)


def test_serverless_spec_is_empty():
    spec = ComputeSpec()
    assert spec.is_serverless is True
    assert spec.to_submit_task_fields() == {}


def test_existing_cluster_spec():
    spec = ComputeSpec(existing_cluster_id="0123-456-abc")
    assert spec.is_serverless is False
    assert spec.to_submit_task_fields() == {"existing_cluster_id": "0123-456-abc"}


def test_new_cluster_spec_passes_through_dict():
    cluster = {"spark_version": "15.4.x-scala2.12", "node_type_id": "i3.xlarge", "num_workers": 2}
    spec = ComputeSpec(new_cluster=cluster)
    fields = spec.to_submit_task_fields()
    assert fields["new_cluster"] == cluster
    # Returned copy is independent so caller mutations don't leak in.
    fields["new_cluster"]["num_workers"] = 99
    assert spec.new_cluster["num_workers"] == 2


def test_instance_pool_spec_wraps_in_new_cluster():
    spec = ComputeSpec(instance_pool_id="pool-xyz")
    fields = spec.to_submit_task_fields()
    assert fields["new_cluster"]["instance_pool_id"] == "pool-xyz"
    assert fields["new_cluster"]["num_workers"] == 0


def test_libraries_round_trip():
    libs = [{"pypi": {"package": "boto3"}}, {"maven": {"coordinates": "org.foo:bar:1.0"}}]
    spec = ComputeSpec(existing_cluster_id="abc", libraries=libs)
    fields = spec.to_submit_task_fields()
    assert fields["libraries"] == libs


def test_multiple_modes_rejected():
    with pytest.raises(ValueError, match="at most one of"):
        ComputeSpec(existing_cluster_id="abc", instance_pool_id="pool-1")


def test_default_classic_compute_single_node():
    spec = default_classic_compute()
    fields = spec.to_submit_task_fields()
    cluster = fields["new_cluster"]
    assert cluster["num_workers"] == 0
    assert cluster["spark_version"] == "15.4.x-scala2.12"
    assert cluster["node_type_id"] == "i3.xlarge"
    # Single-node mode requires these two extra fields or the cluster
    # creation will be rejected by the API.
    assert cluster["spark_conf"]["spark.databricks.cluster.profile"] == "singleNode"
    assert cluster["custom_tags"]["ResourceClass"] == "SingleNode"


def test_default_classic_compute_honours_existing_cluster_precedence():
    spec = default_classic_compute(
        existing_cluster_id="cluster-abc",
        instance_pool_id="pool-xyz",  # should be ignored
        node_type_id="m5.large",       # should be ignored
    )
    assert spec.existing_cluster_id == "cluster-abc"
    assert spec.new_cluster is None


def test_default_classic_compute_uses_instance_pool_when_no_cluster():
    spec = default_classic_compute(
        instance_pool_id="pool-xyz",
        spark_version="15.0.x-scala2.12",
        num_workers=4,
    )
    assert spec.existing_cluster_id is None
    assert spec.new_cluster["instance_pool_id"] == "pool-xyz"
    assert spec.new_cluster["num_workers"] == 4
    assert spec.new_cluster["spark_version"] == "15.0.x-scala2.12"
