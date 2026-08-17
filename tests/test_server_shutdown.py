from unittest.mock import MagicMock

import uvicorn

from app.services.browser.engine import BrowserEngine
from run import ApplicationServer


def make_server():
    return ApplicationServer(uvicorn.Config(lambda scope, receive, send: None))


def test_application_shutdown_intent_precedes_uvicorn_exit(mocker):
    calls = []
    sig = object()
    frame = object()

    request_shutdown = mocker.patch(
        "app.services.browser.engine.request_application_shutdown",
        side_effect=lambda: calls.append("application-shutdown"),
    )
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server,
        "handle_exit",
        autospec=True,
        side_effect=lambda server, received_sig, received_frame: calls.append(
            ("uvicorn-exit", received_sig, received_frame)
        ),
    )

    make_server().handle_exit(sig, frame)

    assert calls == ["application-shutdown", ("uvicorn-exit", sig, frame)]
    request_shutdown.assert_called_once_with()
    uvicorn_exit.assert_called_once()
    assert uvicorn_exit.call_args.args[1:] == (sig, frame)


def test_application_shutdown_hook_does_not_require_existing_engine(mocker):
    frame = MagicMock()
    sig = object()
    mocker.patch.object(BrowserEngine, "_instance", None)
    uvicorn_exit = mocker.patch.object(
        uvicorn.Server,
        "handle_exit",
        autospec=True,
    )

    make_server().handle_exit(sig, frame)

    uvicorn_exit.assert_called_once()
    assert uvicorn_exit.call_args.args[1:] == (sig, frame)
