"""Keep the retired container backend out of the plugin release boundary."""
import importlib.util

import pytest

from core import plugin_runtime_inventory as inventory


@pytest.mark.parametrize('module', [
    'plugin_catalog', 'plugin_gateway', 'plugin_resources', 'plugin_resource_io',
    'plugin_image_hardening', 'plugin_fetch', 'plugin_fetch_image', 'plugin_fetch_lock',
    'plugin_fetch_worker', 'plugin_fetch_https', 'plugin_fetch_ssh', 'plugin_fetch_transport',
])
def test_retired_container_modules_are_unavailable(module):
    assert importlib.util.find_spec('core.' + module) is None


def test_guest_bundle_excludes_host_authority():
    files = set(inventory.BUNDLED_FILES)
    assert not files.intersection({
        'plugin_capabilities.py', 'plugin_host_files.py', 'plugin_host_tools.py',
        'plugin_update_recovery.py',
        'plugin_workspace_recovery.py',
        'native_catalog_leases.py',
        'native_catalog_ownership.py',
        'native_catalog_cleanup.py',
        'native_preparation.py',
        'plugin_artifact.py',
        'native_task_control.py',
        'plugin_tool_config.py', 'host_file_access.py', 'plugin_upload.py',
        'native_git_https.py', 'native_git_ssh.py', 'plugin_http.py',
        'windows_appcontainer.py', 'windows_sandbox_acl.py', 'windows_job_cpu.py',
        'windows_profile_recovery.py',
    })
    # Process supervision is shared SDK code, not a host permission broker.
    assert {'windows_job.py', 'windows_job_lifecycle.py'} <= files
    assert set(inventory.native_fetch_snapshot()) == {
        'core/__init__.py', 'core/native_fetch_worker.py', 'core/plugin_git_archive.py',
    }


def test_standalone_docker_software_manager_remains_available():
    from software.recipes.docker import DockerRecipe
    assert DockerRecipe.__module__ == 'software.recipes.docker.recipe'
