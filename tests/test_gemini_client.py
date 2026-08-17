import pytest
import json
import asyncio
import configparser
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from app.services.providers.gemini.client import close_gemini_client, init_gemini_client
import app.services.providers.gemini.client as gemini_client_module
import app.services.providers.gemini.session_manager as session_manager_module
from app.services.providers.gemini.streaming_response import GeminiLeaseStreamingResponse
from app.services.browser.auth_loader import GeminiAuthStateLoader
from app.services.providers.gemini.auth_selector import GeminiAuthCandidate, GeminiAuthSelector
from app.services.providers.base_repository import ConversationSnapshot, ProviderCapability
from app.services.providers.gemini.session_manager import SessionRegistry, SNAPSHOT_SCHEMA_VERSION


@pytest.fixture(autouse=True)
def reset_gemini_client_state():
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None
    gemini_client_module._gemini_client_auth_source = None
    gemini_client_module._gemini_generation_records.clear()
    gemini_client_module._gemini_client_generations.clear()
    gemini_client_module._current_gemini_generation = None
    gemini_client_module._gemini_shutdown_started = False
    yield
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None
    gemini_client_module._gemini_client_auth_source = None
    gemini_client_module._gemini_generation_records.clear()
    gemini_client_module._gemini_client_generations.clear()
    gemini_client_module._current_gemini_generation = None
    gemini_client_module._gemini_shutdown_started = False


class Status:
    def __init__(self, name):
        self.name = name


def make_mock_client(status_name):
    client = AsyncMock()
    client.client = MagicMock(account_status=Status(status_name))
    return client


def auth_data(psid):
    return {
        "cookies": [
            {"name": "__Secure-1PSID", "value": psid, "domain": ".google.com"}
        ]
    }


def auth_candidate(source_name, source_type, psid, is_legacy=False):
    return GeminiAuthCandidate(
        source_name=source_name,
        source_type=source_type,
        auth_data=auth_data(psid),
        is_legacy=is_legacy,
        supports_webapi_cookie_auth=True,
        supports_playwright_storage=True,
        migration_needed=is_legacy,
    )


def _patch_session_repository(mocker):
    repository = mocker.Mock()
    repository.initialize_sync = mocker.Mock()
    repository.save_snapshot = AsyncMock()
    repository.get_snapshot = AsyncMock(return_value=None)
    repository.delete_snapshot = AsyncMock()
    repository.list_snapshots = AsyncMock(return_value=[])
    mocker.patch(
        "app.services.providers.sqlite_repository.SQLiteConversationRepository",
        return_value=repository,
    )
    return repository


def test_register_generation_accepts_explicit_generation_without_publishing():
    client = make_mock_client("AVAILABLE")

    record = gemini_client_module._register_generation(client, 7)

    assert record.client is client
    assert record.generation == 7
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._current_gemini_generation is None


def test_register_generation_reuses_same_client_and_generation():
    client = make_mock_client("AVAILABLE")

    first = gemini_client_module._register_generation(client, 7)
    second = gemini_client_module._register_generation(client, 7)

    assert second is first


def test_register_generation_rejects_generation_collision():
    first = make_mock_client("AVAILABLE")
    second = make_mock_client("AVAILABLE")
    gemini_client_module._register_generation(first, 7)

    with pytest.raises(RuntimeError, match="already assigned"):
        gemini_client_module._register_generation(second, 7)


def test_register_generation_rejects_incompatible_client_identity_mapping():
    client = make_mock_client("AVAILABLE")
    other = make_mock_client("AVAILABLE")
    gemini_client_module._register_generation(other, 7)
    gemini_client_module._gemini_client_generations[id(client)] = 7

    with pytest.raises(RuntimeError, match="already mapped"):
        gemini_client_module._register_generation(client, 8)


@pytest.mark.asyncio
async def test_init_session_managers_uses_registered_generation_without_allocating(
    mocker,
    install_registry_generation,
):
    client = make_mock_client("AVAILABLE")
    generation = install_registry_generation(client, generation=7)
    _patch_session_repository(mocker)
    mocker.patch.object(session_manager_module, "_gemini_chat_registry", None)
    await session_manager_module.init_session_managers(client, generation)

    registry = session_manager_module.get_gemini_chat_registry()
    assert registry.client is client
    assert registry.client_generation == generation


@pytest.mark.asyncio
async def test_init_session_managers_reopens_existing_registry_with_exact_generation(
    mocker,
    install_registry_generation,
):
    client1 = make_mock_client("AVAILABLE")
    client2 = make_mock_client("AVAILABLE")
    generation1 = install_registry_generation(client1, generation=4)
    generation2 = install_registry_generation(client2, generation=5)
    _patch_session_repository(mocker)
    mocker.patch.object(session_manager_module, "_gemini_chat_registry", None)
    await session_manager_module.init_session_managers(client1, generation1)
    registry = session_manager_module.get_gemini_chat_registry()
    manager = await registry.get_session("preserve-me")

    await session_manager_module.init_session_managers(client2, generation2)

    assert registry.client is client2
    assert registry.client_generation == generation2
    assert registry._sessions["preserve-me"] is manager
    assert manager.client is client2
    assert manager.client_generation == generation2
    assert manager.session_generation is None


@pytest.mark.asyncio
async def test_init_session_managers_rejects_unregistered_initial_generation(mocker):
    client = make_mock_client("AVAILABLE")
    _patch_session_repository(mocker)
    mocker.patch.object(session_manager_module, "_gemini_chat_registry", None)
    with pytest.raises(RuntimeError, match="generation is not registered"):
        await session_manager_module.init_session_managers(client, 7)

    assert session_manager_module.get_gemini_chat_registry() is None


@pytest.mark.asyncio
async def test_init_session_managers_rejects_unregistered_reopen_without_mutation(
    mocker,
    install_registry_generation,
):
    client1 = make_mock_client("AVAILABLE")
    client2 = make_mock_client("AVAILABLE")
    generation = install_registry_generation(client1, generation=4)
    _patch_session_repository(mocker)
    mocker.patch.object(session_manager_module, "_gemini_chat_registry", None)
    await session_manager_module.init_session_managers(client1, generation)
    registry = session_manager_module.get_gemini_chat_registry()
    manager = await registry.get_session("preserve-me")
    state = (registry.client, registry.client_generation, manager.client, manager.client_generation)

    with pytest.raises(RuntimeError, match="generation is not registered"):
        await session_manager_module.init_session_managers(client2, 99)

    assert (registry.client, registry.client_generation, manager.client, manager.client_generation) == state


@pytest.mark.asyncio
async def test_gemini_lifecycle_passes_registered_generation_to_session_managers(mocker):
    candidate = make_mock_client("AVAILABLE")
    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("startup", "config", "startup_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=candidate)
    _patch_session_repository(mocker)
    mocker.patch.object(session_manager_module, "_gemini_chat_registry", None)
    updater = mocker.patch.object(
        session_manager_module,
        "init_session_managers",
        wraps=session_manager_module.init_session_managers,
    )

    assert await init_gemini_client(registry_updater=updater) is True

    updater.assert_awaited_once_with(candidate, 0)
    registry = session_manager_module.get_gemini_chat_registry()
    assert gemini_client_module._current_gemini_generation == 0
    assert registry.client is candidate
    assert registry.client_generation == 0


async def _block_until_release(entered, release):
    entered.set()
    await release.wait()


def patch_auth_sources(mocker, gemini=None, legacy=None, json_source=None):
    return (
        mocker.patch.object(
            GeminiAuthStateLoader,
            'get_gemini_config_source',
            return_value=(gemini, False),
        ),
        mocker.patch.object(
            GeminiAuthStateLoader,
            'get_legacy_cookie_source',
            return_value=(legacy, legacy is not None),
        ),
        mocker.patch.object(
            GeminiAuthStateLoader,
            'get_json_source',
            return_value=(json_source, False),
        ),
    )


@pytest.mark.asyncio
async def test_disabled_gemini_clears_previous_auth_source(mocker, install_gemini_client):
    old_client = make_mock_client("AVAILABLE")
    install_gemini_client(old_client)
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "false"}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)

    assert await init_gemini_client() is False
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._gemini_client_auth_source is None
    assert gemini_client_module._initialization_error == "Gemini client is disabled in config."
    old_client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_failed_init_clears_previous_auth_source(mocker, install_gemini_client):
    old_client = make_mock_client("AVAILABLE")
    install_gemini_client(old_client)
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
    })
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(GeminiAuthStateLoader, "get_gemini_config_source", return_value=(None, False))
    mocker.patch.object(GeminiAuthStateLoader, "get_legacy_cookie_source", return_value=(None, False))
    mocker.patch.object(GeminiAuthStateLoader, "get_json_source", return_value=(None, False))
    mocker.patch("app.services.providers.gemini.client.get_cookie_from_browser", return_value=None)

    assert await init_gemini_client() is False
    assert gemini_client_module._gemini_client is old_client
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    assert gemini_client_module._initialization_error is None


@pytest.mark.asyncio
async def test_replacement_keeps_old_client_published_during_candidate_init(mocker, install_gemini_client):
    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    entered = asyncio.Event()
    release = asyncio.Event()
    async def block_init(**kwargs):
        await _block_until_release(entered, release)

    new_client.init.side_effect = block_init
    install_gemini_client(old_client)
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    task = asyncio.create_task(init_gemini_client())
    await entered.wait()
    assert gemini_client_module._gemini_client is old_client
    assert gemini_client_module.get_gemini_client_auth_source() == "[Gemini] config"
    release.set()
    assert await task is True


@pytest.mark.asyncio
async def test_registry_callback_receives_private_candidate_until_commit(mocker, install_gemini_client):
    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    entered = asyncio.Event()
    release = asyncio.Event()
    install_gemini_client(old_client)
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    async def update_registry(candidate, generation):
        assert candidate is new_client
        assert gemini_client_module.is_gemini_generation_registered(
            client=candidate,
            generation=generation,
        )
        assert gemini_client_module.get_gemini_client() is old_client
        entered.set()
        await release.wait()

    task = asyncio.create_task(init_gemini_client(registry_updater=update_registry))
    await entered.wait()
    assert gemini_client_module.get_gemini_client() is old_client
    release.set()

    assert await task is True
    assert gemini_client_module.get_gemini_client() is new_client
    old_client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_restore_can_lease_generation_during_registry_commit(mocker, install_gemini_client, install_registry_generation):
    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    snapshot = ConversationSnapshot(
        conversation_id="restore-during-commit",
        provider_name="gemini",
        session_state={"state": "saved"},
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        updated_at=datetime.now(timezone.utc),
    )
    repository = SimpleNamespace(
        get_snapshot=mocker.AsyncMock(return_value=snapshot),
        save_snapshot=mocker.AsyncMock(),
        delete_snapshot=mocker.AsyncMock(),
        list_snapshots=mocker.AsyncMock(return_value=[]),
    )
    generation = install_registry_generation(old_client)
    registry = SessionRegistry(
        old_client,
        repository=repository,
        generation=generation,
    )
    install_gemini_client(old_client, generation=generation)
    entered = asyncio.Event()
    release = asyncio.Event()

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    async def update_registry(candidate, generation):
        await registry.update_client(candidate, generation=generation)
        entered.set()
        await release.wait()

    task = asyncio.create_task(init_gemini_client(registry_updater=update_registry))
    await entered.wait()

    assert gemini_client_module.get_gemini_client() is old_client
    assert registry.client is new_client
    assert registry.client_generation == 1
    assert gemini_client_module.is_gemini_generation_registered(
        client=new_client,
        generation=1,
    )

    adapter = SimpleNamespace(
        capabilities={ProviderCapability.PERSISTENT_RECOVERY},
        provider_name="gemini",
        validate_session_recovery=lambda state, context: state,
        deserialize_session_state=lambda state, client, **kwargs: SimpleNamespace(
            model=kwargs.get("model"),
            gem=kwargs.get("gem"),
            client=client,
        ),
    )
    manager = await registry.get_session(
        "restore-during-commit",
        adapter,
        allow_create=False,
    )
    assert manager.client is new_client
    assert manager.client_generation == 1

    release.set()
    assert await task is True
    assert gemini_client_module.get_gemini_client() is new_client


@pytest.mark.asyncio
async def test_startup_registry_failure_keeps_candidate_private(mocker):
    candidate = make_mock_client("AVAILABLE")
    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("startup", "config", "startup_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=candidate)

    async def fail_registry_update(client, generation):
        assert client is candidate
        assert gemini_client_module._gemini_client is None
        assert gemini_client_module.is_gemini_generation_registered(
            client=client,
            generation=generation,
        )
        raise RuntimeError("registry setup failed")

    assert await init_gemini_client(registry_updater=fail_registry_update) is False
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._gemini_client_auth_source is None
    candidate.close.assert_awaited_once_with()
    assert gemini_client_module._gemini_generation_records == {}
    assert gemini_client_module._gemini_client_generations == {}


@pytest.mark.asyncio
async def test_startup_candidate_publishes_after_registry_setup(mocker):
    candidate = make_mock_client("AVAILABLE")
    entered = asyncio.Event()
    release = asyncio.Event()
    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("startup", "config", "startup_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=candidate)

    async def setup_registry(client, generation):
        assert client is candidate
        assert gemini_client_module._gemini_client is None
        assert generation == 0
        assert gemini_client_module.is_gemini_generation_registered(
            client=client,
            generation=generation,
        )
        entered.set()
        await release.wait()

    task = asyncio.create_task(init_gemini_client(registry_updater=setup_registry))
    await entered.wait()
    assert gemini_client_module._gemini_client is None
    release.set()

    assert await task is True
    assert gemini_client_module._gemini_client is candidate


@pytest.mark.asyncio
async def test_replacements_remain_serialized_while_registry_commit_blocks(mocker):
    first = make_mock_client("AVAILABLE")
    second = make_mock_client("AVAILABLE")
    entered = asyncio.Event()
    release = asyncio.Event()
    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    selector = mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        side_effect=[
            iter([auth_candidate("first", "config", "first_psid")]),
            iter([auth_candidate("second", "config", "second_psid")]),
        ],
    )
    mocker.patch(
        "app.services.providers.gemini.client.MyGeminiClient",
        side_effect=[first, second],
    )

    async def update_registry(client, generation):
        if client is first:
            entered.set()
            await release.wait()

    first_task = asyncio.create_task(init_gemini_client(registry_updater=update_registry))
    await entered.wait()
    second_task = asyncio.create_task(init_gemini_client(registry_updater=update_registry))
    assert not second_task.done()
    release.set()

    assert await first_task is True
    assert await second_task is True
    assert selector.call_count == 2
    assert gemini_client_module.get_gemini_client() is second


@pytest.mark.asyncio
async def test_failed_candidate_closes_candidate_and_preserves_old_client(mocker, install_gemini_client):
    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    new_client.init.side_effect = RuntimeError("candidate failed")
    install_gemini_client(old_client)
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    assert await init_gemini_client() is False
    assert gemini_client_module._gemini_client is old_client
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    new_client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_successful_replacement_updates_registry_and_retains_old_client(mocker, install_gemini_client):
    from app.services.providers.gemini.session_manager import SessionRegistry

    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    generation = install_gemini_client(old_client)
    registry = SessionRegistry(
        old_client,
        generation=generation,
    )
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"
    old_lease = gemini_client_module.acquire_current_gemini_lease()

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    assert await init_gemini_client(
        registry_updater=lambda candidate, generation: registry.update_client(
            candidate,
            generation=generation,
        )
    ) is True
    assert gemini_client_module._gemini_client is new_client
    assert gemini_client_module.get_gemini_client_auth_source() == "replacement"
    assert registry.client is new_client
    assert registry.client_generation == 1
    old_client.close.assert_not_awaited()
    old_record = gemini_client_module._gemini_generation_records[0]
    assert old_record.retired is True
    await old_lease.release()
    old_client.close.assert_awaited_once_with()


def test_current_lease_reuses_registered_generation(install_gemini_client):
    client = make_mock_client("AVAILABLE")
    generation = install_gemini_client(client)

    lease = gemini_client_module.acquire_current_gemini_lease()

    assert lease.client is client
    assert lease.generation == generation
    assert list(gemini_client_module._gemini_generation_records) == [generation]
    record = gemini_client_module._gemini_generation_records[generation]
    assert record.client is client
    assert record.lease_count == 1


def _assert_strict_current_lease_rejects(monkeypatch, expected_message, setup):
    setup()
    generation_before = gemini_client_module._current_gemini_generation
    records_before = dict(gemini_client_module._gemini_generation_records)
    mappings_before = dict(gemini_client_module._gemini_client_generations)
    with pytest.raises(RuntimeError, match=expected_message):
        gemini_client_module.acquire_current_gemini_lease()

    assert gemini_client_module._current_gemini_generation == generation_before
    assert gemini_client_module._gemini_generation_records == records_before
    assert gemini_client_module._gemini_client_generations == mappings_before


def test_current_lease_rejects_missing_current_generation_without_mutation(monkeypatch):
    client = make_mock_client("AVAILABLE")

    _assert_strict_current_lease_rejects(
        monkeypatch,
        "current generation is not set",
        lambda: setattr(gemini_client_module, "_gemini_client", client),
    )


def test_current_lease_rejects_missing_generation_record_without_mutation(monkeypatch):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 7

    _assert_strict_current_lease_rejects(monkeypatch, "generation record is missing", setup)


def test_current_lease_rejects_record_for_another_client_without_mutation(monkeypatch):
    other = make_mock_client("AVAILABLE")
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(other, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 0

    _assert_strict_current_lease_rejects(monkeypatch, "does not match current client", setup)


def test_current_lease_rejects_missing_reverse_mapping_without_mutation(monkeypatch):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(client, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 0
        gemini_client_module._gemini_client_generations.pop(id(client))

    _assert_strict_current_lease_rejects(monkeypatch, "reverse generation mapping", setup)


def test_current_lease_rejects_wrong_reverse_mapping_without_mutation(monkeypatch):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(client, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 0
        gemini_client_module._gemini_client_generations[id(client)] = 1

    _assert_strict_current_lease_rejects(monkeypatch, "reverse generation mapping", setup)


async def _assert_replacement_rejects_malformed_state(mocker, setup):
    setup()
    client_before = gemini_client_module._gemini_client
    generation_before = gemini_client_module._current_gemini_generation
    records_before = dict(gemini_client_module._gemini_generation_records)
    mappings_before = dict(gemini_client_module._gemini_client_generations)
    auth_source_before = gemini_client_module._gemini_client_auth_source
    initialization_error_before = gemini_client_module._initialization_error

    selector = mocker.patch.object(GeminiAuthSelector, "iter_candidates", return_value=iter(()))
    constructor = mocker.patch("app.services.providers.gemini.client.MyGeminiClient")
    register = mocker.patch.object(gemini_client_module, "_register_generation")
    registry_updater = AsyncMock()

    with pytest.raises(RuntimeError, match="Gemini lifecycle invariant violated"):
        await init_gemini_client(registry_updater=registry_updater)

    assert gemini_client_module._gemini_client is client_before
    assert gemini_client_module._current_gemini_generation == generation_before
    assert gemini_client_module._gemini_generation_records == records_before
    assert gemini_client_module._gemini_client_generations == mappings_before
    assert gemini_client_module._gemini_client_auth_source == auth_source_before
    assert gemini_client_module._initialization_error == initialization_error_before
    selector.assert_not_called()
    constructor.assert_not_called()
    register.assert_not_called()
    registry_updater.assert_not_awaited()


@pytest.mark.asyncio
async def test_replacement_rejects_missing_current_generation_without_mutation(mocker):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._gemini_client = client
        gemini_client_module._gemini_client_auth_source = "old source"
        gemini_client_module._initialization_error = "old error"

    await _assert_replacement_rejects_malformed_state(mocker, setup)


@pytest.mark.asyncio
async def test_replacement_rejects_missing_generation_record_without_mutation(mocker):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 7
        gemini_client_module._gemini_client_auth_source = "old source"

    await _assert_replacement_rejects_malformed_state(mocker, setup)


@pytest.mark.asyncio
async def test_replacement_rejects_record_for_another_client_without_mutation(mocker):
    other = make_mock_client("AVAILABLE")
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(other, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 0
        gemini_client_module._gemini_client_auth_source = "old source"

    await _assert_replacement_rejects_malformed_state(mocker, setup)


@pytest.mark.asyncio
async def test_replacement_rejects_missing_reverse_mapping_without_mutation(mocker):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(client, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 0
        gemini_client_module._gemini_client_generations.pop(id(client))
        gemini_client_module._gemini_client_auth_source = "old source"

    await _assert_replacement_rejects_malformed_state(mocker, setup)


@pytest.mark.asyncio
async def test_replacement_rejects_wrong_reverse_mapping_without_mutation(mocker):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(client, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 0
        gemini_client_module._gemini_client_generations[id(client)] = 1
        gemini_client_module._gemini_client_auth_source = "old source"

    await _assert_replacement_rejects_malformed_state(mocker, setup)


def test_current_lease_uses_initialization_error_detail():
    gemini_client_module._initialization_error = "Gemini cookies are unavailable."

    with pytest.raises(
        gemini_client_module.GeminiClientNotInitializedError,
        match="Gemini cookies are unavailable",
    ):
        gemini_client_module.acquire_current_gemini_lease()


async def _assert_shutdown_closes_current_client_without_repair(mocker, client, setup):
    setup()
    register = mocker.patch.object(gemini_client_module, "_register_generation")

    await close_gemini_client()

    client.close.assert_awaited_once_with()
    register.assert_not_called()
    assert not any(record.client is client for record in gemini_client_module._gemini_generation_records.values())
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._current_gemini_generation is None
    assert gemini_client_module._gemini_client_auth_source is None
    assert gemini_client_module._initialization_error is None


@pytest.mark.asyncio
async def test_shutdown_defensively_closes_untracked_current_client(mocker):
    client = make_mock_client("AVAILABLE")

    await _assert_shutdown_closes_current_client_without_repair(
        mocker,
        client,
        lambda: setattr(gemini_client_module, "_gemini_client", client),
    )


@pytest.mark.asyncio
async def test_shutdown_missing_current_generation_does_not_create_record(mocker):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 7

    await _assert_shutdown_closes_current_client_without_repair(mocker, client, setup)


@pytest.mark.asyncio
async def test_shutdown_record_mismatch_does_not_create_record(mocker):
    other = make_mock_client("AVAILABLE")
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(other, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 0

    await _assert_shutdown_closes_current_client_without_repair(mocker, client, setup)
    other.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_shutdown_missing_reverse_mapping_closes_record_without_repair(mocker):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(client, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 0
        gemini_client_module._gemini_client_generations.pop(id(client))

    await _assert_shutdown_closes_current_client_without_repair(mocker, client, setup)


@pytest.mark.asyncio
async def test_shutdown_wrong_reverse_mapping_closes_record_without_repair(mocker):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(client, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 0
        gemini_client_module._gemini_client_generations[id(client)] = 1

    await _assert_shutdown_closes_current_client_without_repair(mocker, client, setup)


@pytest.mark.asyncio
async def test_shutdown_does_not_double_close_recorded_client_with_malformed_pointer(mocker):
    client = make_mock_client("AVAILABLE")

    def setup():
        gemini_client_module._register_generation(client, 0)
        gemini_client_module._gemini_client = client
        gemini_client_module._current_gemini_generation = 7
        gemini_client_module._gemini_client_generations[id(client)] = 9

    await _assert_shutdown_closes_current_client_without_repair(mocker, client, setup)


@pytest.mark.asyncio
async def test_shutdown_preserves_active_lease_for_recorded_client_with_malformed_pointer(mocker):
    client = make_mock_client("AVAILABLE")
    gemini_client_module._register_generation(client, 0)
    gemini_client_module._gemini_client = client
    gemini_client_module._current_gemini_generation = 0
    lease = gemini_client_module.acquire_current_gemini_lease()
    gemini_client_module._current_gemini_generation = 7
    gemini_client_module._gemini_client_generations.pop(id(client))

    await close_gemini_client()

    client.close.assert_not_awaited()
    await lease.release()
    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_shutdown_untracked_close_failure_does_not_block_record_cleanup(mocker):
    current = make_mock_client("AVAILABLE")
    other = make_mock_client("AVAILABLE")
    current.close.side_effect = RuntimeError("close failed")
    gemini_client_module._register_generation(other, 0)
    gemini_client_module._gemini_client = current
    gemini_client_module._current_gemini_generation = 0

    await close_gemini_client()

    current.close.assert_awaited_once_with()
    other.close.assert_awaited_once_with()
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._current_gemini_generation is None


@pytest.mark.asyncio
async def test_lease_release_is_idempotent_and_does_not_close_current_client(install_gemini_client):
    client = make_mock_client("AVAILABLE")
    install_gemini_client(client)
    lease = gemini_client_module.acquire_current_gemini_lease()

    await lease.release()
    await lease.release()

    assert gemini_client_module._gemini_generation_records[0].lease_count == 0
    client.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_manager_rejects_released_external_lease(install_gemini_client):
    from app.services.providers.gemini.session_manager import SessionRegistry

    client = make_mock_client("AVAILABLE")
    generation = install_gemini_client(client)
    lease = gemini_client_module.acquire_current_gemini_lease()
    await lease.release()
    manager = await SessionRegistry(
        client,
        generation=generation,
    ).get_session("released-lease")

    with pytest.raises(RuntimeError, match="no longer active"):
        await manager.get_response_stateful("model", [{"content": "hi"}], "", lease=lease)
    client.start_chat.assert_not_called()


@pytest.mark.asyncio
async def test_lease_streaming_response_releases_before_body_starts(install_gemini_client):
    client = make_mock_client("AVAILABLE")
    body_started = False
    cleanup = AsyncMock()
    install_gemini_client(client)
    lease = gemini_client_module.acquire_current_gemini_lease()
    record = gemini_client_module._gemini_generation_records[lease.generation]
    gemini_client_module._retire_generation(record)

    async def body():
        nonlocal body_started
        body_started = True
        yield b"never"

    response = GeminiLeaseStreamingResponse(
        body(),
        lease=lease,
        cleanup=cleanup,
    )
    lease.transfer()

    async def send(message):
        raise OSError("disconnect before body")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    with pytest.raises(Exception):
        await response(
            {"type": "http", "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )

    assert body_started is False
    cleanup.assert_awaited_once_with()
    client.close.assert_awaited_once_with()


def test_lease_cannot_transfer_ownership_twice(install_gemini_client):
    client = make_mock_client("AVAILABLE")
    install_gemini_client(client)
    lease = gemini_client_module.acquire_current_gemini_lease()

    lease.transfer()
    with pytest.raises(RuntimeError, match="already transferred"):
        lease.transfer()


@pytest.mark.asyncio
async def test_retired_generation_rejects_new_lease_but_existing_lease_releases(install_gemini_client):
    client = make_mock_client("AVAILABLE")
    install_gemini_client(client)
    active_lease = gemini_client_module.acquire_current_gemini_lease()
    record = gemini_client_module._gemini_generation_records[0]
    gemini_client_module._retire_generation(record)

    with pytest.raises(gemini_client_module.GeminiGenerationUnavailableError, match="retired"):
        gemini_client_module.acquire_gemini_lease(client=client, generation=0)

    await active_lease.release()
    client.close.assert_awaited_once_with()


def test_invalid_client_generation_pair_rejected(install_gemini_client):
    client = make_mock_client("AVAILABLE")
    other = make_mock_client("AVAILABLE")
    install_gemini_client(client)
    lease = gemini_client_module.acquire_current_gemini_lease()

    with pytest.raises(gemini_client_module.GeminiGenerationUnavailableError, match="do not match"):
        gemini_client_module.acquire_gemini_lease(client=other, generation=lease.generation)


@pytest.mark.asyncio
async def test_multiple_retired_generations_close_independently(install_gemini_client):
    first = make_mock_client("AVAILABLE")
    second = make_mock_client("AVAILABLE")
    current = make_mock_client("AVAILABLE")
    install_gemini_client(first)
    first_lease = gemini_client_module.acquire_current_gemini_lease()
    first_record = gemini_client_module._gemini_generation_records[0]

    second_record = gemini_client_module._register_generation(second, 1)
    gemini_client_module._current_gemini_generation = 1
    # Explicit multi-generation lifecycle state; both pointer and record stay coherent.
    gemini_client_module._gemini_client = second
    gemini_client_module._retire_generation(first_record)
    second_lease = gemini_client_module.acquire_current_gemini_lease()

    current_record = gemini_client_module._register_generation(current, 2)
    gemini_client_module._current_gemini_generation = 2
    # Explicit multi-generation lifecycle state; both pointer and record stay coherent.
    gemini_client_module._gemini_client = current
    gemini_client_module._retire_generation(second_record)

    await first_lease.release()
    first.close.assert_awaited_once_with()
    second.close.assert_not_awaited()
    await second_lease.release()
    second.close.assert_awaited_once_with()
    assert current_record.retired is False
    current.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_rejects_new_leases_and_preserves_active_lease(install_gemini_client):
    client = make_mock_client("AVAILABLE")
    install_gemini_client(client)
    lease = gemini_client_module.acquire_current_gemini_lease()

    await close_gemini_client()

    assert gemini_client_module._gemini_client is None
    client.close.assert_not_awaited()
    with pytest.raises(RuntimeError, match="shutting down"):
        gemini_client_module.acquire_current_gemini_lease()

    await lease.release()
    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_shutdown_is_idempotent_after_release(install_gemini_client):
    client = make_mock_client("AVAILABLE")
    install_gemini_client(client)
    lease = gemini_client_module.acquire_current_gemini_lease()

    await close_gemini_client()
    await lease.release()
    await close_gemini_client()

    client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_successful_restart_reactivates_lease_acquisition(mocker):
    first = make_mock_client("AVAILABLE")
    second = make_mock_client("AVAILABLE")
    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        side_effect=[
            iter([auth_candidate("first", "config", "first_psid")]),
            iter([auth_candidate("second", "config", "second_psid")]),
        ],
    )
    mocker.patch(
        "app.services.providers.gemini.client.MyGeminiClient",
        side_effect=[first, second],
    )

    async def registry_update(client, generation):
        assert generation == 0

    assert await init_gemini_client(registry_updater=registry_update) is True
    first_lease = gemini_client_module.acquire_current_gemini_lease()
    await first_lease.release()
    await close_gemini_client()
    with pytest.raises(RuntimeError, match="shutting down"):
        gemini_client_module.acquire_current_gemini_lease()

    assert await init_gemini_client(registry_updater=registry_update) is True
    second_lease = gemini_client_module.acquire_current_gemini_lease()
    assert second_lease.client is second
    assert second_lease.generation == 0
    assert gemini_client_module._current_gemini_generation == 0
    await second_lease.release()
    first.close.assert_awaited_once_with()
    second.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_restart_keeps_lifecycle_shutdown(mocker):
    first = make_mock_client("AVAILABLE")
    failed = make_mock_client("AVAILABLE")
    failed.init.side_effect = RuntimeError("restart failed")
    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        side_effect=[
            iter([auth_candidate("first", "config", "first_psid")]),
            iter([auth_candidate("failed", "config", "failed_psid")]),
        ],
    )
    mocker.patch(
        "app.services.providers.gemini.client.MyGeminiClient",
        side_effect=[first, failed],
    )

    assert await init_gemini_client() is True
    await close_gemini_client()
    assert await init_gemini_client() is False

    with pytest.raises(RuntimeError, match="shutting down"):
        gemini_client_module.acquire_current_gemini_lease()
    failed.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_restart_preserves_active_old_lease_and_new_generation(mocker):
    first = make_mock_client("AVAILABLE")
    second = make_mock_client("AVAILABLE")
    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        side_effect=[
            iter([auth_candidate("first", "config", "first_psid")]),
            iter([auth_candidate("second", "config", "second_psid")]),
        ],
    )
    mocker.patch(
        "app.services.providers.gemini.client.MyGeminiClient",
        side_effect=[first, second],
    )

    async def registry_update(client, generation):
        assert generation == (0 if client is first else 1)

    assert await init_gemini_client(registry_updater=registry_update) is True
    old_lease = gemini_client_module.acquire_current_gemini_lease()
    await close_gemini_client()
    first.close.assert_not_awaited()

    assert await init_gemini_client(registry_updater=registry_update) is True
    new_lease = gemini_client_module.acquire_current_gemini_lease()
    assert new_lease.client is second
    assert new_lease.generation == 1
    assert gemini_client_module._gemini_generation_records[0].retired is True

    await old_lease.release()
    first.close.assert_awaited_once_with()
    second.close.assert_not_awaited()
    await new_lease.release()


@pytest.mark.asyncio
async def test_shutdown_closes_record_generations_after_failure(install_gemini_client):
    failed = make_mock_client("AVAILABLE")
    remaining = make_mock_client("AVAILABLE")
    failed.close.side_effect = RuntimeError("close failed")
    install_gemini_client(failed)
    failed_record = gemini_client_module._gemini_generation_records[0]
    remaining_record = gemini_client_module._register_generation(remaining, 1)
    gemini_client_module._retire_generation(remaining_record)

    await close_gemini_client()

    failed.close.assert_awaited_once_with()
    remaining.close.assert_awaited_once_with()
    assert failed_record.close_started is True


@pytest.mark.asyncio
async def test_registry_update_failure_rolls_back_replacement(mocker, install_gemini_client):
    from app.services.providers.gemini.session_manager import SessionRegistry

    old_client = make_mock_client("AVAILABLE")
    new_client = make_mock_client("AVAILABLE")
    generation = install_gemini_client(old_client)
    registry = SessionRegistry(
        old_client,
        generation=generation,
    )
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"

    config = configparser.ConfigParser()
    config.read_dict({"EnabledAI": {"gemini": "true"}, "Proxy": {"http_proxy": ""}})
    mocker.patch("app.services.providers.gemini.client.CONFIG", config)
    mocker.patch.object(
        GeminiAuthSelector,
        "iter_candidates",
        return_value=iter([auth_candidate("replacement", "config", "new_psid")]),
    )
    mocker.patch("app.services.providers.gemini.client.MyGeminiClient", return_value=new_client)

    async def fail_registry_update(candidate, generation):
        assert candidate is new_client
        raise RuntimeError("registry failed")

    assert await init_gemini_client(registry_updater=fail_registry_update) is False
    assert gemini_client_module._gemini_client is old_client
    assert gemini_client_module.get_gemini_client_auth_source() == "[Gemini] config"
    assert registry.client is old_client
    assert registry.client_generation == 0
    assert list(gemini_client_module._gemini_generation_records) == [0]
    new_client.close.assert_awaited_once_with()
    old_client.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_gemini_client_closes_and_resets_state(mocker, install_gemini_client):
    client = make_mock_client("AVAILABLE")
    install_gemini_client(client)
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"
    gemini_client_module._initialization_error = "old error"

    await close_gemini_client()

    client.close.assert_awaited_once_with()
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._gemini_client_auth_source is None
    assert gemini_client_module._initialization_error is None


@pytest.mark.asyncio
async def test_shutdown_close_failure_does_not_block_other_generations(install_gemini_client):
    current = make_mock_client("AVAILABLE")
    failed = make_mock_client("AVAILABLE")
    remaining = make_mock_client("AVAILABLE")
    failed.close.side_effect = RuntimeError("close failed")
    install_gemini_client(current)
    current_record = gemini_client_module._gemini_generation_records[0]
    failed_record = gemini_client_module._register_generation(failed, 1)
    remaining_record = gemini_client_module._register_generation(remaining, 2)
    gemini_client_module._retire_generation(failed_record)
    gemini_client_module._retire_generation(remaining_record)

    await close_gemini_client()

    current.close.assert_awaited_once_with()
    failed.close.assert_awaited_once_with()
    remaining.close.assert_awaited_once_with()
    assert current_record.close_completed is True
    assert failed_record.close_started is True
    assert remaining_record.close_completed is True


@pytest.mark.asyncio
async def test_close_gemini_client_is_safe_without_client_and_on_repeat():
    await close_gemini_client()
    await close_gemini_client()


@pytest.mark.asyncio
async def test_close_gemini_client_resets_state_when_close_fails(mocker, install_gemini_client):
    client = make_mock_client("AVAILABLE")
    client.close.side_effect = RuntimeError("close failed")
    install_gemini_client(client)
    gemini_client_module._gemini_client_auth_source = "[Gemini] config"
    gemini_client_module._initialization_error = "old error"

    await close_gemini_client()

    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._gemini_client_auth_source is None
    assert gemini_client_module._initialization_error is None


@pytest.mark.asyncio
async def test_init_gemini_client_available(mocker):
    """Verify that a client with AVAILABLE status is successfully retained and registered."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG using a real ConfigParser populated via read_dict
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Gemini": {"__Secure-1PSID": "valid_psid", "__Secure-1PSIDTS": "valid_psidts"},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    # Mock MyGeminiClient
    mock_client_instance = make_mock_client("AVAILABLE")
    
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_client_instance
    )

    # Mock get_cookie_from_browser so it's not called
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_client_instance
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    mock_my_gemini_client_class.assert_called_once()
    mock_client_instance.init.assert_called_once_with(verbose=True, auto_refresh=False)
    mock_get_cookies.assert_not_called()


@pytest.mark.asyncio
async def test_init_gemini_client_unauthenticated_retained(mocker):
    """Verify that a client with UNAUTHENTICATED status is retained as a candidate when loader returns it."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock MyGeminiClient
    mock_client_instance = make_mock_client("UNAUTHENTICATED")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_client_instance
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser to return empty
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser', return_value=None)

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_client_instance
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    mock_my_gemini_client_class.assert_called_once()
    mock_client_instance.init.assert_called_once_with(verbose=True, auto_refresh=False)
    
    mock_gemini_source.assert_called_once()
    mock_legacy_source.assert_called_once()
    mock_json_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_location_rejected_discarded_and_fallback(mocker):
    """Verify that a client with LOCATION_REJECTED is discarded and browser fallback is triggered."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock initial loader client (LOCATION_REJECTED)
    mock_loader_client = make_mock_client("LOCATION_REJECTED")

    # Mock browser fallback client (AVAILABLE)
    mock_fallback_client = make_mock_client("AVAILABLE")

    # Side effect for MyGeminiClient creation: 1st loader, 2nd browser fallback
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_loader_client, mock_fallback_client]
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser to return valid browser cookies
    mock_get_cookies = mocker.patch(
        'app.services.providers.gemini.client.get_cookie_from_browser',
        return_value={"__Secure-1PSID": "browser_psid"}
    )

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_fallback_client
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "browser cookie fallback"
    
    # Verify all client instances were handled
    assert mock_my_gemini_client_class.call_count == 2
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_called_once()
    
    mock_fallback_client.init.assert_called_once()

    # Verify fallback cookies were requested
    mock_gemini_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_playwright_state_fallback(mocker):
    """Verify that when loader cookies are UNAUTHENTICATED, client successfully upgrades to browser AVAILABLE cookies."""
    # Reset global states
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock loader client (UNAUTHENTICATED)
    mock_loader_client = make_mock_client("UNAUTHENTICATED")

    # Mock browser fallback client (AVAILABLE)
    mock_fallback_client = make_mock_client("AVAILABLE")

    # Side effect for MyGeminiClient creation: 1st loader, 2nd browser fallback
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_loader_client, mock_fallback_client]
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser to return valid browser cookies
    mock_get_cookies = mocker.patch(
        'app.services.providers.gemini.client.get_cookie_from_browser',
        return_value={"__Secure-1PSID": "browser_psid"}
    )

    # Execute
    res = await init_gemini_client()

    # Assertions
    assert res is True
    assert gemini_client_module._gemini_client == mock_fallback_client
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "browser cookie fallback"

    # Verify calls
    assert mock_my_gemini_client_class.call_count == 2
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_called_once()  # Closed upon upgrade
    mock_fallback_client.init.assert_called_once()
    
    mock_gemini_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_config_unauth_playwright_unauth(mocker):
    """Verify that when both loader and browser cookies are UNAUTHENTICATED, loader candidate is retained and browser client is closed."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock loader client (UNAUTHENTICATED)
    mock_loader_client = make_mock_client("UNAUTHENTICATED")

    # Mock browser client (UNAUTHENTICATED)
    mock_browser_client = make_mock_client("UNAUTHENTICATED")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_loader_client, mock_browser_client]
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser to return valid browser cookies
    mock_get_cookies = mocker.patch(
        'app.services.providers.gemini.client.get_cookie_from_browser',
        return_value={"__Secure-1PSID": "browser_psid"}
    )

    # Execute
    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client == mock_loader_client
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"

    assert mock_my_gemini_client_class.call_count == 2
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_not_called()  # Retained
    
    mock_browser_client.init.assert_called_once()
    mock_browser_client.close.assert_called_once()  # Closed as duplicate
    
    mock_gemini_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


@pytest.mark.asyncio
async def test_init_gemini_client_playwright_available_browser_available(mocker):
    """Verify that when Loader is AVAILABLE and Browser is AVAILABLE, Loader is selected and Browser is bypassed."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    # Mock loader client (AVAILABLE)
    mock_loader_client = make_mock_client("AVAILABLE")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_loader_client
    )

    # Mock GeminiAuthStateLoader to return valid auth data
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(
        mocker,
        gemini=auth_data("some_psid"),
    )

    # Mock get_cookie_from_browser so it's not called
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client == mock_loader_client
    assert gemini_client_module._initialization_error is None
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"

    mock_my_gemini_client_class.assert_called_once()
    mock_loader_client.init.assert_called_once()
    mock_loader_client.close.assert_not_called()
    
    mock_gemini_source.assert_called_once()
    mock_get_cookies.assert_not_called()  # Bypassed


@pytest.mark.asyncio
async def test_init_gemini_client_all_unavailable(mocker):
    """Verify that when all sources are unavailable, client initialization fails and returns False."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    # Mock CONFIG
    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    mock_my_gemini_client_class = mocker.patch('app.services.providers.gemini.client.MyGeminiClient')
    
    # Mock GeminiAuthStateLoader to return None (no cookies available)
    mock_gemini_source, mock_legacy_source, mock_json_source = patch_auth_sources(mocker)
    
    mock_get_cookies = mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser', return_value=None)

    # Execute
    res = await init_gemini_client()

    assert res is False
    assert gemini_client_module._gemini_client is None
    assert gemini_client_module._initialization_error == "Gemini cookies not found or completely invalid in canonical store, legacy config, or browser."
    assert gemini_client_module._gemini_client_auth_source is None

    mock_my_gemini_client_class.assert_not_called()
    mock_gemini_source.assert_called_once()
    mock_legacy_source.assert_called_once()
    mock_json_source.assert_called_once()
    mock_get_cookies.assert_called_once_with("gemini")


# =============================================================================
# Source Iteration Tests (Config Source Priority Chain)
# =============================================================================

@pytest.mark.asyncio
async def test_init_gemini_client_consumes_selector_candidates_in_order(mocker):
    """Verify WebAPI initialization consumes GeminiAuthSelector candidates in order."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    candidates = [
        auth_candidate("[Gemini] config", "gemini_config", "gemini_psid"),
        auth_candidate("[Cookies] legacy config", "legacy_cookies", "cookies_psid", is_legacy=True),
    ]
    selector = mocker.patch(
        'app.services.providers.gemini.client.GeminiAuthSelector.iter_candidates',
        return_value=iter(candidates),
    )

    mock_gemini_client = make_mock_client("UNAUTHENTICATED")
    mock_cookies_client = make_mock_client("AVAILABLE")
    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_gemini_client, mock_cookies_client]
    )
    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client is mock_cookies_client
    assert gemini_client_module._gemini_client_auth_source == "[Cookies] legacy config"
    selector.assert_called_once()
    assert [call.kwargs["secure_1psid"] for call in mock_my_gemini_client_class.call_args_list] == [
        "gemini_psid",
        "cookies_psid",
    ]


@pytest.mark.asyncio
async def test_init_gemini_client_source_iteration_unauth_to_avail(mocker):
    """Verify that when [Gemini] is UNAUTHENTICATED, [Cookies] is tried and AVAILABLE is selected."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    # Track which sources were attempted
    attempted_sources = []

    def mock_gemini_source():
        attempted_sources.append("gemini")
        return {"cookies": [{"name": "__Secure-1PSID", "value": "gemini_psid", "domain": ".google.com"}]}, False

    def mock_cookies_source():
        attempted_sources.append("cookies")
        return {"cookies": [{"name": "__Secure-1PSID", "value": "cookies_psid", "domain": ".google.com"}]}, True

    def mock_json_source():
        attempted_sources.append("json")
        return None, False

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', side_effect=mock_gemini_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', side_effect=mock_cookies_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_json_source', side_effect=mock_json_source)

    # Mock clients: [Gemini] -> UNAUTHENTICATED, [Cookies] -> AVAILABLE
    mock_gemini_client = make_mock_client("UNAUTHENTICATED")

    mock_cookies_client = make_mock_client("AVAILABLE")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_gemini_client, mock_cookies_client]
    )

    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    # Assertions - externally observable behavior only
    assert res is True  # Function succeeded
    assert gemini_client_module._gemini_client is mock_cookies_client  # AVAILABLE client selected
    assert gemini_client_module._gemini_client_auth_source == "[Cookies] legacy config"
    assert attempted_sources == ["gemini", "cookies"]  # Both tried in correct order


@pytest.mark.asyncio
async def test_init_gemini_client_source_chain_multiple_unauth_to_avail(mocker):
    """Verify that all config sources are tried when earlier ones are UNAUTHENTICATED."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    attempted_sources = []

    def mock_gemini_source():
        attempted_sources.append("gemini")
        return auth_data("gemini_psid"), False

    def mock_cookies_source():
        attempted_sources.append("cookies")
        return auth_data("cookies_psid"), True

    def mock_json_source():
        attempted_sources.append("json")
        return auth_data("json_psid"), False

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', side_effect=mock_gemini_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', side_effect=mock_cookies_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_json_source', side_effect=mock_json_source)

    mock_gemini_client = make_mock_client("UNAUTHENTICATED")

    mock_cookies_client = make_mock_client("UNAUTHENTICATED")

    mock_json_client = make_mock_client("AVAILABLE")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_gemini_client, mock_cookies_client, mock_json_client]
    )

    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    # Assertions - externally observable behavior only
    assert res is True  # Function succeeded
    assert gemini_client_module._gemini_client is mock_json_client  # AVAILABLE client selected
    assert gemini_client_module._gemini_client_auth_source == "gemini.json canonical store"
    assert attempted_sources == ["gemini", "cookies", "json"]  # All tried in correct order


@pytest.mark.asyncio
async def test_init_gemini_client_available_short_circuit(mocker):
    """Verify that when [Gemini] is AVAILABLE, no lower-priority sources are attempted."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    attempted_sources = []

    def mock_gemini_source():
        attempted_sources.append("gemini")
        return auth_data("gemini_psid"), False

    def mock_cookies_source():
        attempted_sources.append("cookies")
        return auth_data("cookies_psid"), True

    def mock_json_source():
        attempted_sources.append("json")
        return auth_data("json_psid"), False

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', side_effect=mock_gemini_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', side_effect=mock_cookies_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_json_source', side_effect=mock_json_source)

    mock_gemini_client = make_mock_client("AVAILABLE")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_gemini_client
    )

    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    # Execute
    res = await init_gemini_client()

    # Assertions - externally observable behavior only
    assert res is True  # Function succeeded
    assert gemini_client_module._gemini_client is mock_gemini_client  # First client selected
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    assert len(attempted_sources) == 1  # Only first source tried
    assert attempted_sources == ["gemini"]


@pytest.mark.asyncio
async def test_init_gemini_client_guest_mode_fallback_preserved(mocker):
    """Verify that when all sources are UNAUTHENTICATED, guest-mode fallback is retained."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)
    mocker.patch('app.services.browser.auth_loader.CONFIG', mock_config)

    attempted_sources = []

    def mock_gemini_source():
        attempted_sources.append("gemini")
        return auth_data("gemini_psid"), False

    def mock_cookies_source():
        attempted_sources.append("cookies")
        return auth_data("cookies_psid"), True

    def mock_json_source():
        attempted_sources.append("json")
        return auth_data("json_psid"), False

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', side_effect=mock_gemini_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', side_effect=mock_cookies_source)
    mocker.patch.object(GeminiAuthStateLoader, 'get_json_source', side_effect=mock_json_source)

    # All clients UNAUTHENTICATED
    mock_gemini_client = make_mock_client("UNAUTHENTICATED")

    mock_cookies_client = make_mock_client("UNAUTHENTICATED")

    mock_json_client = make_mock_client("UNAUTHENTICATED")

    mock_browser_client = make_mock_client("UNAUTHENTICATED")

    mock_my_gemini_client_class = mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        side_effect=[mock_gemini_client, mock_cookies_client, mock_json_client, mock_browser_client]
    )

    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser',
                return_value={"__Secure-1PSID": "browser_psid"})

    # Execute
    res = await init_gemini_client()

    # Assertions - verify guest-mode client is retained and initialization succeeds
    assert res is True  # Function succeeded with guest-mode fallback
    assert gemini_client_module._gemini_client is mock_gemini_client  # Highest-priority guest fallback retained
    assert gemini_client_module._gemini_client_auth_source == "[Gemini] config"
    assert attempted_sources == ["gemini", "cookies", "json"]  # All config sources tried


@pytest.mark.asyncio
async def test_init_gemini_client_json_store_source_label(mocker):
    """Verify canonical gemini.json initialization records the canonical source label."""
    gemini_client_module._gemini_client = None
    gemini_client_module._initialization_error = None
    gemini_client_module._gemini_client_auth_source = None

    mock_config = configparser.ConfigParser()
    mock_config.optionxform = str
    mock_config.read_dict({
        "EnabledAI": {"gemini": "true"},
        "Proxy": {"http_proxy": ""},
        "Playwright": {"auth_state_dir": "auth_state"}
    })
    mocker.patch('app.services.providers.gemini.client.CONFIG', mock_config)

    mock_json_client = make_mock_client("AVAILABLE")
    mocker.patch(
        'app.services.providers.gemini.client.MyGeminiClient',
        return_value=mock_json_client
    )

    mocker.patch.object(GeminiAuthStateLoader, 'get_gemini_config_source', return_value=(None, False))
    mocker.patch.object(GeminiAuthStateLoader, 'get_legacy_cookie_source', return_value=(None, False))
    mocker.patch.object(
        GeminiAuthStateLoader,
        'get_json_source',
        return_value=({"cookies": [{"name": "__Secure-1PSID", "value": "json_psid", "domain": ".google.com"}]}, False),
    )
    mocker.patch('app.services.providers.gemini.client.get_cookie_from_browser')

    res = await init_gemini_client()

    assert res is True
    assert gemini_client_module._gemini_client is mock_json_client
    assert gemini_client_module._gemini_client_auth_source == "gemini.json canonical store"


@pytest.mark.asyncio
async def test_refresh_status_preserves_webapi_source_when_authenticated(mocker, install_gemini_client):
    """Verify refresh_status reports the current WebAPI source without clearing it."""
    from app.services.providers.gemini.auth import GeminiAuthStrategy

    install_gemini_client(make_mock_client("AVAILABLE"))
    gemini_client_module._gemini_client_auth_source = "[Cookies] legacy config"

    mocker.patch(
        "app.services.providers.gemini.auth_selector.GeminiAuthSelector.iter_candidates",
        return_value=iter([auth_candidate("[Cookies] legacy config", "legacy_cookies", "cookies_psid", is_legacy=True)]),
    )

    status = GeminiAuthStrategy().refresh_status()

    assert status["webapi"] == "AUTHENTICATED"
    assert status["webapi_source"] == "[Cookies] legacy config"
    assert gemini_client_module._gemini_client_auth_source == "[Cookies] legacy config"
