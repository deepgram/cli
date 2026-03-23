"""Tests for members command."""

from unittest.mock import Mock

import pytest
from deepctl_cmd_members.command import MembersCommand
from deepctl_cmd_members.models import InviteInfo, MemberInfo, MembersResult
from deepctl_core import AuthManager, BaseResult, Config, DeepgramClient


class TestMembersCommand:
    """Test cases for MembersCommand."""

    @pytest.fixture
    def command(self):
        """Create a MembersCommand instance."""
        return MembersCommand()

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

    def test_command_properties(self, command):
        """Test command basic properties."""
        assert command.name == "members"
        assert command.requires_auth is True
        assert command.requires_project is True

    def test_get_arguments(self, command):
        """Test command arguments configuration."""
        args = command.get_arguments()

        # All arguments should be options
        option_names = []
        for arg in args:
            assert arg.get("is_option", False) is True
            option_names.extend(arg["names"])

        assert "--list" in option_names
        assert "-l" in option_names
        assert "--invite" in option_names
        assert "-i" in option_names
        assert "--scope" in option_names
        assert "--remove" in option_names
        assert "-r" in option_names
        assert "--invites" in option_names
        assert "--revoke-invite" in option_names
        assert "--project-id" in option_names
        assert "-p" in option_names

    def test_handle_list_members(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test default behavior lists members."""
        mock_client.list_members.return_value = {
            "members": [
                {
                    "member_id": "m-1",
                    "email": "user@example.com",
                    "first_name": "Test",
                    "last_name": "User",
                    "scopes": ["member"],
                }
            ]
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, MembersResult)
        assert result.status == "success"
        assert result.count == 1
        assert len(result.members) == 1
        assert result.members[0].member_id == "m-1"
        assert result.members[0].email == "user@example.com"
        assert result.members[0].first_name == "Test"
        assert result.members[0].last_name == "User"
        assert result.members[0].scopes == ["member"]
        mock_client.list_members.assert_called_once_with(project_id=None)

    def test_handle_list_empty(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test empty members list returns info status."""
        mock_client.list_members.return_value = {"members": []}

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert isinstance(result, MembersResult)
        assert result.status == "info"
        assert result.message == "No members found"

    def test_handle_invite(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test inviting a member by email."""
        mock_client.create_invite.return_value = {}

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            invite="user@example.com",
            scope="admin",
        )

        assert isinstance(result, BaseResult)
        assert result.status == "success"
        assert "user@example.com" in result.message
        mock_client.create_invite.assert_called_once_with(
            email="user@example.com", scope="admin", project_id=None
        )

    def test_handle_remove(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test removing a member by ID."""
        mock_client.remove_member.return_value = {}

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            remove="m-1",
            yes=True,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "success"
        assert "m-1" in result.message
        mock_client.remove_member.assert_called_once_with(
            "m-1", project_id=None
        )

    def test_handle_list_invites(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test listing pending invites."""
        mock_client.list_invites.return_value = {
            "invites": [
                {"email": "invite@example.com", "scope": "member"}
            ]
        }

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            invites=True,
        )

        assert isinstance(result, MembersResult)
        assert result.status == "success"
        assert result.count == 1
        assert len(result.invites) == 1
        assert result.invites[0].email == "invite@example.com"
        assert result.invites[0].scope == "member"
        mock_client.list_invites.assert_called_once_with(project_id=None)

    def test_handle_revoke_invite(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test revoking an invite by email."""
        mock_client.delete_invite.return_value = {}

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
            revoke_invite="user@example.com",
            yes=True,
        )

        assert isinstance(result, BaseResult)
        assert result.status == "success"
        assert "user@example.com" in result.message
        mock_client.delete_invite.assert_called_once_with(
            "user@example.com", project_id=None
        )

    def test_handle_error(
        self, command, mock_config, mock_auth_manager, mock_client
    ):
        """Test client raises exception, returns error."""
        mock_client.list_members.side_effect = Exception(
            "API connection failed"
        )

        result = command.handle(
            config=mock_config,
            auth_manager=mock_auth_manager,
            client=mock_client,
        )

        assert result.status == "error"
        assert "API connection failed" in result.message


class TestMembersModels:
    """Test cases for members models."""

    def test_member_info_defaults(self):
        """Test MemberInfo with default values."""
        info = MemberInfo()
        assert info.member_id == ""
        assert info.email == ""
        assert info.first_name == ""
        assert info.last_name == ""
        assert info.scopes == []

    def test_member_info_with_values(self):
        """Test MemberInfo with provided values."""
        info = MemberInfo(
            member_id="m-123",
            email="user@example.com",
            first_name="Jane",
            last_name="Doe",
            scopes=["member", "admin"],
        )
        assert info.member_id == "m-123"
        assert info.email == "user@example.com"
        assert info.first_name == "Jane"
        assert info.last_name == "Doe"
        assert info.scopes == ["member", "admin"]

    def test_invite_info(self):
        """Test InviteInfo model."""
        invite = InviteInfo(email="new@example.com", scope="admin")
        assert invite.email == "new@example.com"
        assert invite.scope == "admin"

    def test_members_result_defaults(self):
        """Test MembersResult with default values."""
        result = MembersResult()
        assert result.members == []
        assert result.invites == []
        assert result.count == 0

    def test_members_result_serialization(self):
        """Test MembersResult can be serialized."""
        result = MembersResult(
            status="success",
            members=[MemberInfo(member_id="m-1", email="user@test.com")],
            count=1,
        )
        data = result.model_dump()
        assert data["count"] == 1
        assert len(data["members"]) == 1
        assert data["members"][0]["member_id"] == "m-1"
        assert data["members"][0]["email"] == "user@test.com"
