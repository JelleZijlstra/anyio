import asyncio
import sys

import pytest

import anyio
from anyio import (
    Event, create_task_group, get_current_task, get_running_tasks, move_on_after,
    wait_all_tasks_blocked)

pytestmark = pytest.mark.anyio


def test_main_task_name(anyio_backend_name, anyio_backend_options):
    async def main():
        nonlocal task_name
        task_name = get_current_task().name

    task_name = None
    anyio.run(main, backend=anyio_backend_name, backend_options=anyio_backend_options)
    assert task_name == 'test_debugging.test_main_task_name.<locals>.main'

    # Work around sniffio/asyncio bug that leaves behind an unclosed event loop
    if anyio_backend_name == 'asyncio':
        import asyncio
        import gc
        for loop in [obj for obj in gc.get_objects()
                     if isinstance(obj, asyncio.AbstractEventLoop)]:
            loop.close()


@pytest.mark.parametrize(
    "name_input,expected",
    [
        (None, 'test_debugging.test_non_main_task_name.<locals>.non_main'),
        (b'name', "b'name'"),
        ("name", "name"),
        ("", ""),
    ],
)
async def test_non_main_task_name(name_input, expected):
    async def non_main(*, task_status):
        task_status.started(anyio.get_current_task().name)

    async with anyio.create_task_group() as tg:
        name = await tg.start(non_main, name=name_input)

    assert name == expected


async def test_get_running_tasks():
    async def inspect():
        await wait_all_tasks_blocked()
        new_tasks = set(get_running_tasks()) - existing_tasks
        task_infos[:] = sorted(new_tasks, key=lambda info: info.name or '')
        event.set()

    event = Event()
    task_infos = []
    host_task = get_current_task()
    async with create_task_group() as tg:
        existing_tasks = set(get_running_tasks())
        tg.start_soon(event.wait, name='task1')
        tg.start_soon(event.wait, name='task2')
        tg.start_soon(inspect)

    assert len(task_infos) == 3
    expected_names = ['task1', 'task2', 'test_debugging.test_get_running_tasks.<locals>.inspect']
    for task, expected_name in zip(task_infos, expected_names):
        assert task.parent_id == host_task.id
        assert task.name == expected_name
        assert repr(task) == f'TaskInfo(id={task.id}, name={expected_name!r})'


@pytest.mark.filterwarnings('ignore:"@coroutine" decorator is deprecated:DeprecationWarning')
def test_wait_generator_based_task_blocked(asyncio_event_loop):
    async def native_coro_part():
        await wait_all_tasks_blocked()
        assert not gen_task._coro.gi_running
        if sys.version_info < (3, 7):
            assert gen_task._coro.gi_yieldfrom.gi_code.co_name == 'wait'
        else:
            assert gen_task._coro.gi_yieldfrom.cr_code.co_name == 'wait'

        event.set()

    @asyncio.coroutine
    def generator_part():
        yield from event.wait()

    event = asyncio.Event()
    gen_task = asyncio_event_loop.create_task(generator_part())
    asyncio_event_loop.run_until_complete(native_coro_part())


@pytest.mark.parametrize('anyio_backend', ['asyncio'])
async def test_wait_all_tasks_blocked_asend(anyio_backend):
    """Test that wait_all_tasks_blocked() does not crash on an `asend()` object."""

    async def agen_func():
        yield

    agen = agen_func()
    coro = agen.asend(None)
    loop = asyncio.get_event_loop()
    task = loop.create_task(coro)
    await wait_all_tasks_blocked()
    await task
    await agen.aclose()


async def test_wait_all_tasks_blocked_cancelled_task():
    async def self_cancel(*, task_status):
        nonlocal done
        task_status.started()
        with move_on_after(-1):
            await Event().wait()

        done = True

    done = False
    async with create_task_group() as tg:
        await tg.start(self_cancel)
        await wait_all_tasks_blocked()
        assert done
