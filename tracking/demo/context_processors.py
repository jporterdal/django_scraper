"""Template context for demo framing (banner, hidden controls)."""

from . import dataset, is_demo


def demo(request):
    if not is_demo():
        return {"demo_mode": False}
    from ..models import DemoState

    state = DemoState.objects.filter(pk=1).first()
    return {
        "demo_mode": True,
        "demo_last_reset_at": state.last_reset_at if state else None,
        "demo_try_terms": dataset.seed()["try_terms"],
    }
