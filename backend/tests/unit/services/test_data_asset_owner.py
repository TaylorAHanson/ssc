"""Unit tests for data asset owner resolution and settings integration."""
from unittest.mock import patch
import pytest

from app.core.config import settings
from app.db.data_asset import DataAssetModel
from app.services.data_asset_owner import resolve_data_asset_owner


class TestDataAssetOwnerResolution:
    def test_default_tag_from_string_list(self):
        """Resolves data_owner tag value from standard string list."""
        tags = ["data_owner=Data Engineering", "certified", "tier=gold"]
        assert resolve_data_asset_owner("sp-uuid-12345", tags) == "Data Engineering"

    def test_colon_separator(self):
        """Supports colon as tag key-value separator."""
        tags = ["data_owner: supply-chain-team@company.com"]
        assert resolve_data_asset_owner("sp-uuid-12345", tags) == "supply-chain-team@company.com"

    def test_case_insensitive_tag_name(self):
        """Tag name matching is case-insensitive."""
        tags = ["DATA_OWNER=Enterprise Analytics"]
        assert resolve_data_asset_owner("sp-uuid-12345", tags) == "Enterprise Analytics"

        tags_mixed = ["Data_Owner=Enterprise Analytics"]
        assert resolve_data_asset_owner("sp-uuid-12345", tags_mixed) == "Enterprise Analytics"

    def test_quotes_stripping(self):
        """Quotes around tag values are cleanly stripped."""
        tags = ['data_owner="Sales Operations"']
        assert resolve_data_asset_owner("sp-uuid-12345", tags) == "Sales Operations"

        tags_single = ["data_owner='Sales Operations'"]
        assert resolve_data_asset_owner("sp-uuid-12345", tags_single) == "Sales Operations"

    def test_dict_tags(self):
        """Resolves owner when tags are structured as a dictionary."""
        tags = {"data_owner": "Platform Team", "certified": "true"}
        assert resolve_data_asset_owner("sp-uuid-12345", tags) == "Platform Team"

    def test_dict_list_tags(self):
        """Resolves owner when tags are a list of tag dictionaries."""
        tags = [
            {"tag_name": "data_owner", "tag_value": "Finance Stewards"},
            {"tag_name": "classification", "tag_value": "restricted"},
        ]
        assert resolve_data_asset_owner("sp-uuid-12345", tags) == "Finance Stewards"

    def test_json_string_tags(self):
        """Resolves owner when tags column contains a JSON string."""
        tags = '["data_owner=Logistics Team", "certified"]'
        assert resolve_data_asset_owner("sp-uuid-12345", tags) == "Logistics Team"

    def test_fallback_when_tag_missing(self):
        """Falls back to direct owner when data_owner tag is not present."""
        tags = ["domain=Supply Chain", "certified"]
        assert resolve_data_asset_owner("sp-uuid-12345", tags) == "sp-uuid-12345"

    def test_fallback_when_tag_value_empty(self):
        """Falls back to direct owner when data_owner tag has empty value."""
        tags = ["data_owner=", "certified"]
        assert resolve_data_asset_owner("sp-uuid-12345", tags) == "sp-uuid-12345"

        tags_bare = ["data_owner", "certified"]
        assert resolve_data_asset_owner("sp-uuid-12345", tags_bare) == "sp-uuid-12345"

    def test_fallback_when_setting_is_blank(self):
        """Falls back to direct owner when the owner tag setting is disabled/blank."""
        tags = ["data_owner=Data Engineering"]
        # Explicit owner_tag=""
        assert resolve_data_asset_owner("sp-uuid-12345", tags, owner_tag="") == "sp-uuid-12345"

        # Via settings
        with patch.object(settings, "DATA_ASSET_OWNER_TAG", ""):
            assert resolve_data_asset_owner("sp-uuid-12345", tags) == "sp-uuid-12345"

    def test_custom_configurable_tag(self):
        """Supports configuring a different owner tag in settings."""
        tags = ["team_lead=Alice Smith", "data_owner=Legacy Service Principal"]
        assert resolve_data_asset_owner("sp-uuid-12345", tags, owner_tag="team_lead") == "Alice Smith"

        with patch.object(settings, "DATA_ASSET_OWNER_TAG", "business_owner"):
            tags = ["business_owner=Bob Jones", "data_owner=Ignored"]
            assert resolve_data_asset_owner("sp-uuid-12345", tags) == "Bob Jones"

    def test_data_asset_model_effective_owner_property(self):
        """DataAssetModel.effective_owner property resolves the accountable owner."""
        asset = DataAssetModel(
            id="cat.sch.tbl",
            catalog="cat",
            schema="sch",
            table_name="tbl",
            type="TABLE",
            owner="56837c5b-f444-4db4-8da7-3440fa3baca1",
            tags=["data_owner=Supply Chain Team", "certified"],
        )
        assert asset.effective_owner == "Supply Chain Team"

        asset_no_tag = DataAssetModel(
            id="cat.sch.tbl2",
            catalog="cat",
            schema="sch",
            table_name="tbl2",
            type="TABLE",
            owner="56837c5b-f444-4db4-8da7-3440fa3baca1",
            tags=["certified"],
        )
        assert asset_no_tag.effective_owner == "56837c5b-f444-4db4-8da7-3440fa3baca1"

    def test_list_data_assets_endpoint_resolves_owner(self):
        """list_data_assets endpoint returns effective data owner."""
        from unittest.mock import MagicMock
        from app.api.v1.data_assets import list_data_assets

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query

        sample = DataAssetModel(
            id="main.sales.orders",
            catalog="main",
            schema="sales",
            table_name="orders",
            type="TABLE",
            owner="sp-uuid-12345",
            tags=["data_owner=Sales & Revenue Team", "tier=gold"],
            last_synced_at=None,
        )
        mock_query.all.return_value = [sample]

        res = list_data_assets(db=mock_db)
        assert len(res) == 1
        assert res[0]["owner"] == "Sales & Revenue Team"

    def test_list_metric_views_endpoint_resolves_owner(self):
        """list_metric_views endpoint returns effective data owner."""
        from unittest.mock import MagicMock
        from app.api.v1.data_assets import list_metric_views

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query

        sample = DataAssetModel(
            id="main.metrics.kpis",
            catalog="main",
            schema="metrics",
            table_name="kpis",
            type="METRIC_VIEW",
            owner="sp-uuid-12345",
            tags=["data_owner=Analytics Guild"],
            last_synced_at=None,
        )
        mock_query.all.return_value = [sample]

        res = list_metric_views(db=mock_db)
        assert len(res) == 1
        assert res[0]["owner"] == "Analytics Guild"
