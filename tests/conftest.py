import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure the FastAPI app package is importable without installing the project
repo_root = Path(__file__).resolve().parents[1]
app_path = repo_root / "nextflow_k8s_service"
if str(app_path) not in sys.path:
    sys.path.insert(0, str(app_path))


@pytest.fixture(autouse=True)
def reset_app_state():
    """Reset app state before and after each test to prevent test pollution.

    This fixture automatically runs for every test and ensures that modifications
    to app.state don't leak between tests.
    """
    try:
        from app.main import app

        # Store original state
        original_state = {}
        if hasattr(app, 'state'):
            for attr in dir(app.state):
                if not attr.startswith('_'):
                    try:
                        original_state[attr] = getattr(app.state, attr)
                    except AttributeError:
                        pass

        yield

        # Restore original state after test
        if hasattr(app, 'state'):
            for attr, value in original_state.items():
                try:
                    setattr(app.state, attr, value)
                except AttributeError:
                    pass

    except ImportError:
        # If app is not available, skip state reset
        yield


@pytest.fixture
def mock_app_state():
    """Provide a properly mocked app state for tests.

    This fixture provides a clean app state with mocked services
    that can be used in tests without side effects.
    """
    from unittest.mock import AsyncMock
    from app.main import app

    # Create mock services
    mock_pipeline_manager = AsyncMock()
    mock_broadcaster = AsyncMock()
    mock_state_store = AsyncMock()

    # Store original state
    original_state = {
        'pipeline_manager': getattr(app.state, 'pipeline_manager', None),
        'broadcaster': getattr(app.state, 'broadcaster', None),
        'state_store': getattr(app.state, 'state_store', None),
    }

    # Set mock state
    app.state.pipeline_manager = mock_pipeline_manager
    app.state.broadcaster = mock_broadcaster
    app.state.state_store = mock_state_store

    yield mock_pipeline_manager, mock_broadcaster, mock_state_store

    # Restore original state
    for key, value in original_state.items():
        if value is not None:
            setattr(app.state, key, value)
