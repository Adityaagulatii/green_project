"""pytest options for contract/relay_conformance.py, and its collection (its
name does not match test_*.py, so that it reads as a suite for any relay)."""
import pytest


def pytest_addoption(parser):
    g = parser.getgroup("relay conformance (contract/relay_conformance.py)")
    g.addoption("--relay-url", help="a running relay's WebSocket URL, on loopback")
    g.addoption("--source-url", help="where sources reserve and send frames (default: "
                                     "--relay-url); a gatekeeper's URL, for example")
    g.addoption("--viewer-url", help="where viewers send view (default: --relay-url)")
    g.addoption("--relay-cmd", help="a shell command that starts a relay on loopback and "
                                    "prints its ws:// URL, and 'udp HOST:PORT' if it listens "
                                    "for BLP/MCUF (default: this repo's demo relay)")
    g.addoption("--relay-udp", help="HOST:PORT of the relay's BLP/MCUF listener, if any")
    g.addoption("--seq-rule", choices=("literal", "serial"), default="literal",
                help="the sequence rule the relay claims (default literal, the spec's text)")
    g.addoption("--relay-default", help="the display an omitted display resolves to, when "
                                        "the relay serves no capabilities.json")
    g.addoption("--skip-choices", action="store_true",
                help="skip what tests this kit's readings where the spec is silent (README.md)")


def pytest_configure(config):
    config.addinivalue_line("markers", "choice: tests this kit's reading where the spec is "
                                       "silent; --skip-choices skips them")
    config.addinivalue_line("markers", "same_url: needs view and reserve on one URL; skipped "
                                       "when --source-url and --viewer-url differ")


def pytest_collection_modifyitems(config, items):
    opt = config.getoption
    split = (opt("--source-url") or opt("--relay-url")) != (opt("--viewer-url")
                                                          or opt("--relay-url"))
    for item in items:
        if opt("--skip-choices") and item.get_closest_marker("choice"):
            item.add_marker(pytest.mark.skip(reason="--skip-choices"))
        if split and item.get_closest_marker("same_url"):
            item.add_marker(pytest.mark.skip(reason="sources and viewers on different URLs"))


def pytest_collect_file(file_path, parent):
    if file_path.name == "relay_conformance.py" and not parent.session.isinitpath(file_path):
        return pytest.Module.from_parent(parent, path=file_path)
