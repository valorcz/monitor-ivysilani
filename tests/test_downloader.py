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


def test_base_cmd_includes_output_template():
    cmd = dl._base_cmd()
    assert "-o" in cmd
    idx = cmd.index("-o")
    assert cmd[idx + 1] == dl.CONFIG.YTDLP_OUTPUT_TEMPLATE
    assert "-P" in cmd
    p_idx = cmd.index("-P")
    assert cmd[p_idx + 1] == dl.CONFIG.DOWNLOAD_DIR


def test_download_many_progress_callback(monkeypatch):
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    class _L:
        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

    events = []

    async def _progress(idx, tot, url, status):
        events.append((idx, tot, url, status))

    async def run():
        urls = [
            "https://ceskatelevize.cz/porady/x/1",
            "https://ceskatelevize.cz/porady/x/2",
        ]
        results = await dl.download_many(urls, logger=_L(), progress_callback=_progress)
        assert len(results) == 2
        assert all(ok for _, ok, _ in results)

        # Expect 4 events: 2 starts (status=None), 2 completions (status=True)
        assert len(events) == 4
        assert events[0] == (1, 2, urls[0], None)
        assert events[1] == (1, 2, urls[0], True)
        assert events[2] == (2, 2, urls[1], None)
        assert events[3] == (2, 2, urls[1], True)

    asyncio.run(run())

