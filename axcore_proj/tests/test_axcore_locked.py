"""공통 골격(axcore/, axtools/)이 잠금 파일과 같은지 확인한다.

골격이 책의 설명과 달라지면 이 테스트가 알려 준다.
골격을 일부러 고쳤다면 python -m tools.update_lock 으로 잠금 파일을 갱신한다.
"""
from tools.update_lock import current, read_lock


def test_shared_skeleton_is_unchanged():
    lock, now = read_lock(), current()
    changed = sorted(k for k in set(lock) | set(now) if lock.get(k) != now.get(k))
    assert not changed, ("공통 골격이 잠금 파일과 다릅니다: " + ", ".join(changed)
                         + ". 일부러 고친 것이면 python -m tools.update_lock 으로 잠금 파일을 갱신하세요.")
