import asyncio
import types
import tvwatch.core.downloader as dl


class _FakeProc:
    def __init__(self, rc=0, out=b"ok\n", err=b""):
        self.returncode = rc
        self._out = out
        self._err = err
        self.stdout = types.SimpleNamespace(readline=self._readline)
        self.stderr = types.SimpleNamespace(read=self._read)

    async def _readline(self):
        if self._out:
            data, self._out = self._out, b""
            return data
        return b""

    async def _read(self):
        return self._err

    async def wait(self):
        return


async def _fake_exec(*args, **kwargs):
    return _FakeProc(rc=0)


def test_download_one_monkeypatch(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    class _L:
        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

    async def run():
        url, ok, err = await dl.download_one(
            "https://ceskatelevize.cz/porady/x/1", logger=_L()
        )
        assert ok and not err

    asyncio.run(run())
