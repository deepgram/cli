"""Tests for billing command."""

from unittest.mock import Mock

import pytest
from deepctl_cmd_billing.command import BillingCommand
from deepctl_cmd_billing.models import BalanceInfo, BillingResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


class TestBillingCommand:
    """Test cases for BillingCommand."""

    @pytest.fixture
    def command(self):
        """Create a BillingCommand instance."""
        return BillingCommand()

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        return Mock(spec=Config)

    @pytest.fixture
    def mock_auth_manager(self):
        """Create a mock auth manager."""
        manager = Mock(spec=AuthManager)
        manager.get_api_key.return_value = "test-api-key"
        return manager

    @pytest.fixture
    def mock_client(self):
        """Create a mock Deepgram client."""
        return Mock(spec=DeepgramClient)

    @pytest.fixture
    def sample_balances_response(self):
        """Sample response from client.get_balances()."""
        return {
            "balances": [
                {
                    "balance_id": "bal-1",
                    "amount": 100.0,
                    "units": "usd",
                },
            ],
        }

    @pytest.fixture
    def sample_breakdown_response(self):
        """Sample response from client.get_billing_breakdown()."""
        return {
            "resolution": {"period": "monthly", "amount": 1},
            "results": [
                {
                    "start": "2024-01-01",
                    "amount": 50.0,
                    "units": "usd",
                },
            ],
        }

    def test_command_properties(self, command):
        """Test command basic properties."""
        assert command.name == "billing"
        assert command.requires_auth is True
        assert command.requires_project is True
        assert command.ci_friendly is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        option_names = []
        for arg in args:
            assert arg.get("is_option", False) is True
            option_names.extend(arg["names"])

        assert "--balances" in option_names
        assert "-b" in option_names
        assert "--breakdown" in option_names
        assert "--start" in option_names
        assert "--end" in option_names
        assert "--grouping" in option_names
        assert "--project-id" in option_names
        assert "-p" in option_names

    def test_handle_default_shows_both(
        self,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_balances_response,
        sample_breakdown_response,
    ):
        """Test no flags = shows balances AND breakdown."""
        mock_client.get_balances.return_value = sample_balances_response
        mock_client.get_billing_breakdown.return_value = sample_breakdown_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, BillingResult)
        assert result.status == "success"
        # Both balances and breakdown should be populated
        assert len(result.balances) == 1
        assert result.balances[0].balance_id == "bal-1"
        assert result.balances[0].amount == 100.0
        assert result.breakdown != {}
        assert result.breakdown["resolution"]["period"] == "monthly"
        mock_client.get_balances.assert_called_once()
        mock_client.get_billing_breakdown.assert_called_once()

    def test_handle_balances_only(
        self,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_balances_response,
    ):
        """Test balances=True, breakdown=False shows only balances."""
        mock_client.get_balances.return_value = sample_balances_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            balances=True,
            breakdown=False,
        )

        assert isinstance(result, BillingResult)
        assert result.status == "success"
        assert len(result.balances) == 1
        assert result.balances[0].balance_id == "bal-1"
        assert result.balances[0].amount == 100.0
        assert result.balances[0].units == "usd"
        # Breakdown should not be fetched
        assert result.breakdown == {}
        mock_client.get_balances.assert_called_once()
        mock_client.get_billing_breakdown.assert_not_called()

    def test_handle_breakdown_only(
        self,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_breakdown_response,
    ):
        """Test breakdown=True, balances=False shows only breakdown."""
        mock_client.get_billing_breakdown.return_value = sample_breakdown_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            balances=False,
            breakdown=True,
        )

        assert isinstance(result, BillingResult)
        assert result.status == "success"
        # Balances should not be fetched
        assert result.balances == []
        assert result.breakdown == sample_breakdown_response
        mock_client.get_balances.assert_not_called()
        mock_client.get_billing_breakdown.assert_called_once()

    def test_handle_empty_balances(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test empty balances list returns result with no balance entries."""
        mock_client.get_balances.return_value = {"balances": []}

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            balances=True,
            breakdown=False,
        )

        assert isinstance(result, BillingResult)
        assert result.status == "success"
        assert result.balances == []
        mock_client.get_balances.assert_called_once()

    def test_handle_breakdown_with_dates(
        self,
        command,
        mock_config,
        mock_auth_manager,
        mock_client,
        sample_breakdown_response,
    ):
        """Test start/end dates are passed through to client."""
        mock_client.get_billing_breakdown.return_value = sample_breakdown_response

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            balances=False,
            breakdown=True,
            start="2024-01-01",
            end="2024-01-31",
            grouping="tags",
        )

        assert isinstance(result, BillingResult)
        assert result.status == "success"
        mock_client.get_billing_breakdown.assert_called_once_with(
            project_id=None,
            start="2024-01-01",
            end="2024-01-31",
            grouping="tags",
        )

    def test_handle_error(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test client exception returns error status."""
        mock_client.get_balances.side_effect = Exception("API connection failed")

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "error"
        assert "API connection failed" in result.message


class TestBillingModels:
    """Test cases for billing models."""

    def test_balance_info_defaults(self):
        """Test BalanceInfo with default values."""
        info = BalanceInfo()
        assert info.balance_id == ""
        assert info.amount == 0.0
        assert info.units == ""

    def test_balance_info_with_values(self):
        """Test BalanceInfo with provided values."""
        info = BalanceInfo(
            balance_id="bal-123",
            amount=250.50,
            units="usd",
        )
        assert info.balance_id == "bal-123"
        assert info.amount == 250.50
        assert info.units == "usd"

    def test_billing_result_defaults(self):
        """Test BillingResult with default values."""
        result = BillingResult()
        assert result.balances == []
        assert result.breakdown == {}

    def test_billing_result_serialization(self):
        """Test BillingResult can be serialized."""
        result = BillingResult(
            status="success",
            balances=[
                BalanceInfo(
                    balance_id="bal-1",
                    amount=100.0,
                    units="usd",
                ),
            ],
            breakdown={"resolution": {"period": "monthly", "amount": 1}},
        )
        data = result.model_dump()
        assert len(data["balances"]) == 1
        assert data["balances"][0]["balance_id"] == "bal-1"
        assert data["balances"][0]["amount"] == 100.0
        assert data["breakdown"]["resolution"]["period"] == "monthly"
