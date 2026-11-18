import io
import pytest

from core.native_fetch import _worker_failure
from core.native_sandbox import NativeSandboxError


def test_worker_failure_reports_only_fixed_exception_category(monkeypatch):
    from core import i18n
    monkeypatch.setattr(i18n, '_current', i18n._flatten(i18n._load_lang('zh')))
    with pytest.raises(NativeSandboxError) as caught:
        _worker_failure(io.BytesIO(
            b'OPSKIT_FETCH_FAILURE:{"kind":"OSError","code":5,"phase":"read-pack"}\n'), 1)
    assert caught.value.code == 'worker_failed'
    assert 'OSError' not in str(caught.value) and '5' not in str(caught.value)


@pytest.mark.parametrize('body', [b'secret stderr', b'OPSKIT_FETCH_ERROR:{"operation":"private-path","phase":"exit","code":1}',
                                  b'OPSKIT_FETCH_ERROR:{"operation":"rev-parse","code":"password"}',
                                  b'OPSKIT_FETCH_ERROR:[]', b'OPSKIT_FETCH_ERROR:' + b'x' * 1000])
def test_untrusted_worker_diagnostics_are_not_displayed(body):
    _worker_failure(io.BytesIO(body), 1)
